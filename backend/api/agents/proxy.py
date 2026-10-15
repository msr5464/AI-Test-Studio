"""
QA Agents Proxy Blueprint
=========================

Thin forwarder between the AI-Test-Studio Flask app and the separate
QA-Agent-Network HTTP server (run via `scripts/run-server.sh` in that repo).

The browser never talks to the QA-Agent-Network server directly — it calls
`/api/agents/*` on this app, which:
  1. Enforces login via the existing `require_auth()` decorator
  2. Forwards the request to QA_AGENT_NETWORK_URL
  3. Streams the response back (plain JSON or text/event-stream)

Configuration:
  - QA_AGENT_NETWORK_URL      (default http://localhost:6001)
  - QA_AGENT_NETWORK_TIMEOUT  (default 30, seconds; only applies to non-stream)
"""

from __future__ import annotations

import os
from typing import Iterable, Tuple
from urllib.parse import unquote

import requests
from flask import (Blueprint, Response, current_app, jsonify, request,
                   stream_with_context, session)
from backend.api.auth.routes import require_auth

agents_bp = Blueprint("agents_proxy", __name__)

@agents_bp.before_request
@require_auth(admin_only=False)
def check_auth():
    """Enforce authentication on all agent proxy routes."""
    pass


def _upstream_base() -> str:
    return os.getenv("QA_AGENT_NETWORK_URL", "http://localhost:6001").rstrip("/")


def _timeout() -> float:
    try:
        return float(os.getenv("QA_AGENT_NETWORK_TIMEOUT", "30"))
    except (TypeError, ValueError):
        return 30.0


# Strip these from both inbound and outbound headers — Flask / the forwarded
# client connection handle them, and copying them across causes double-chunking
# or invalid responses.
_HOP_BY_HOP_HEADERS = {
    "connection", "content-encoding", "content-length", "transfer-encoding",
    "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
    "upgrade", "host",
}


# Headers a client must never be able to set on a proxied request. X-User-* IS
# the identity qa_agents_server trusts, and X-User-Role is its only admin
# assertion, so forwarding a client's own copy is impersonation. Cookie and
# Authorization are stripped because the upstream neither needs nor should log
# this app's session cookie.
#
# This was previously safe only by accident: WSGI normalises an inbound header
# to "X-User-Id" while the injector writes "X-User-ID" — different dict keys —
# and requests happened to resolve the collision last-write-wins. Reordering two
# lines, or swapping requests for httpx, would silently have turned that into a
# full impersonation hole.
_CLIENT_CONTROLLED_HEADERS = {
    "x-user-id", "x-user-name", "x-user-role", "x-proxy-secret",
    "cookie", "authorization",
}


def _filter_headers(headers: Iterable[Tuple[str, str]]) -> dict:
    return {k: v for k, v in headers
            if k.lower() not in _HOP_BY_HOP_HEADERS
            and k.lower() not in _CLIENT_CONTROLLED_HEADERS}


def _inject_user_headers(headers: dict) -> dict:
    """Inject current user context for qa_agents_server."""
    # Drop any casing variant that survived, so ours is unambiguously the only
    # copy rather than merely the last one written.
    for key in list(headers):
        if key.lower() in _CLIENT_CONTROLLED_HEADERS:
            del headers[key]
    headers["X-User-ID"] = session.get("user_id", "default")
    headers["X-User-Name"] = session.get("username", "Unknown")
    # The stored role, not the copy put in the session at login: sessions refresh on
    # every request, so a demoted admin would otherwise stay "admin" upstream.
    auth_service = current_app.config.get("AUTH_SERVICE")
    user = auth_service.get_current_user() if auth_service else None
    headers["X-User-Role"] = user.role if user else "member"
    # Proves to qa_agents_server that the identity headers came from this proxy
    # rather than straight off the network. Optional: unset means the upstream
    # is relying on binding to localhost instead.
    secret = (os.getenv("QA_AGENT_PROXY_SECRET") or "").strip()
    if secret:
        headers["X-Proxy-Secret"] = secret
    return headers


def _unreachable_response() -> Tuple[Response, int]:
    return jsonify({
        "success": False,
        "error": "QA Agent Network unreachable",
        "detail": f"Could not connect to {_upstream_base()}. "
                  f"Start it with `bash scripts/run-server.sh` in the "
                  f"QA-Agent-Network repo, or set QA_AGENT_NETWORK_URL.",
    }), 503


def _forward_json(method: str, path: str):
    """Forward a simple JSON request. Returns a Flask Response."""
    url = f"{_upstream_base()}{path}"
    outbound_headers = _filter_headers(request.headers.items())
    outbound_headers = _inject_user_headers(outbound_headers)
    try:
        upstream = requests.request(
            method=method,
            url=url,
            params=request.args,
            json=request.get_json(silent=True) if method in ("POST", "PUT", "PATCH") else None,
            headers=outbound_headers,
            timeout=_timeout(),
        )
    except requests.Timeout:
        return jsonify({
            "success": False,
            "error": "QA Agent Network request timed out",
        }), 504
    except requests.ConnectionError:
        return _unreachable_response()
    except requests.RequestException as e:
        return jsonify({
            "success": False,
            "error": f"QA Agent Network error: {e}",
        }), 502

    # Pass through body + status + relevant headers
    resp = Response(upstream.content, status=upstream.status_code)
    for k, v in _filter_headers(upstream.headers.items()).items():
        resp.headers[k] = v
    return resp


def _forward_stream(path: str) -> Response:
    """Forward an SSE (or any streaming) GET request."""
    url = f"{_upstream_base()}{path}"
    outbound_headers = _filter_headers(request.headers.items())
    outbound_headers = _inject_user_headers(outbound_headers)
    try:
        upstream = requests.get(
            url,
            params=request.args,
            headers=outbound_headers,
            stream=True,
            timeout=(10, None),  # connect timeout only; no read timeout
        )
    except requests.ConnectionError:
        return _unreachable_response()[0]
    except requests.RequestException as e:
        return jsonify({
            "success": False,
            "error": f"QA Agent Network error: {e}",
        }), 502

    if upstream.status_code >= 400:
        # Propagate the error body unchanged
        resp = Response(upstream.content, status=upstream.status_code)
        for k, v in _filter_headers(upstream.headers.items()).items():
            resp.headers[k] = v
        return resp

    def generate():
        try:
            for chunk in upstream.iter_content(chunk_size=None):
                if chunk:
                    yield chunk
        except (requests.ConnectionError, requests.ChunkedEncodingError):
            return
        finally:
            try:
                upstream.close()
            except Exception:
                pass

    return Response(
        stream_with_context(generate()),
        status=upstream.status_code,
        content_type=upstream.headers.get("Content-Type", "text/event-stream"),
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            # NOT keep-alive. An SSE stream that ends (the run finished) left the
            # socket in a state the browser held rather than released, and with a
            # six-connection-per-origin cap the next request on the page queued
            # behind it — measured at a flat ~20s stall right after a run
            # completed. "close" tears the socket down when the stream ends.
            "Connection": "close",
            # Forwarded deliberately: the artefact endpoint marks a captured DOM
            # snapshot as an attachment so it downloads instead of rendering in
            # the dashboard's own origin, where its scripts would run. Dropping
            # this header would quietly undo that.
            **({"Content-Disposition": upstream.headers["Content-Disposition"]}
               if upstream.headers.get("Content-Disposition") else {}),
        },
    )


# ── Health ────────────────────────────────────────────────────────────────────
@agents_bp.route("/health", methods=["GET"])
def health():
    """Ping the upstream server so the UI can show a connected/disconnected banner."""
    url = f"{_upstream_base()}/health"
    try:
        upstream = requests.get(url, timeout=5)
    except requests.ConnectionError:
        return jsonify({
            "connected": False,
            "upstream_url": _upstream_base(),
            "error": "unreachable",
        }), 200
    except requests.RequestException as e:
        return jsonify({
            "connected": False,
            "upstream_url": _upstream_base(),
            "error": str(e),
        }), 200

    try:
        body = upstream.json()
    except ValueError:
        body = {}
    return jsonify({
        "connected": upstream.status_code == 200,
        "upstream_url": _upstream_base(),
        "upstream": body,
    })


# ── One forwarder for every agent route ──────────────────────────────────────
#
# This used to be ~15 hand-written route decorators per agent, each body a
# one-line `_forward_json("GET", "/agents/test-authoring-agent/queue")`. Two
# agents made that 30 near-identical functions, and a third would have made 45 —
# with the failure mode that a route someone forgot to copy 404s only for the
# new agent, which is the sort of thing nobody notices until a demo.
#
# The upstream server already dispatches on the agent segment, so this only has
# to pass it through. The allowlist stays: this path reaches an internal service,
# and forwarding an arbitrary agent name to it is not something to leave open.
_ALLOWED_AGENTS = {
    "test-authoring-agent",
    "test-healing-agent",
    "test-adaptation-agent",
}


@agents_bp.route("/<agent>/<path:rest>",
                 methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def forward_agent(agent: str, rest: str):
    if agent not in _ALLOWED_AGENTS:
        return jsonify({
            "error": f"unknown agent: {agent}",
            "known": sorted(_ALLOWED_AGENTS),
        }), 404

    # requests normalises dot segments, so "../../settings" would leave
    # /agents/<agent>/ and reach server-wide routes like /settings, which only
    # the admin blueprint is meant to proxy. WSGI decodes %2e%2e once; unquote
    # catches a double-encoded %252e too, which requests turns back into "..".
    if any(unquote(segment) in (".", "..") for segment in rest.split("/")):
        return jsonify({"error": "invalid path"}), 404

    # No query string here: both forwarders already pass params=request.args,
    # so appending it would send every parameter twice.
    path = f"/agents/{agent}/{rest}"

    # SSE has to stream; everything else is a plain JSON round-trip. Deciding on
    # the suffix rather than on a route table keeps the two lists from drifting.
    if request.method == "GET" and rest.endswith("/stream"):
        return _forward_stream(path)
    return _forward_json(request.method, path)
