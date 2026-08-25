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
  - QA_AGENT_NETWORK_URL      (default http://localhost:8765)
  - QA_AGENT_NETWORK_TIMEOUT  (default 30, seconds; only applies to non-stream)
"""

from __future__ import annotations

import os
from typing import Iterable, Tuple

import requests
from flask import (Blueprint, Response, current_app, jsonify, request,
                   stream_with_context)

agents_bp = Blueprint("agents_proxy", __name__)


# ⚠️ NO AUTHENTICATION IS ENFORCED HERE.
#
# The module docstring above says these routes are gated by require_auth(), but
# no route carries the decorator and there is no before_request guard — every
# /api/agents/* endpoint, including POST /run which spawns processes on the host,
# is reachable without a session.
#
# This was tried as a blueprint-wide before_request guard and reverted: the
# customer UI never authenticates (nothing in frontend/customer/index.html calls
# /api/auth/login) and no other customer API requires auth either, so enforcing
# it here 401s the working Tests-to-Automation panel with no way for a user to
# log in. Closing the gap needs a product decision — add a login flow to the
# customer UI, bind the app to localhost, or accept the exposure — so it is
# flagged here rather than changed unilaterally.


def _upstream_base() -> str:
    return os.getenv("QA_AGENT_NETWORK_URL", "http://localhost:8765").rstrip("/")


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


def _filter_headers(headers: Iterable[Tuple[str, str]]) -> dict:
    return {k: v for k, v in headers if k.lower() not in _HOP_BY_HOP_HEADERS}


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
    try:
        upstream = requests.request(
            method=method,
            url=url,
            params=request.args,
            json=request.get_json(silent=True) if method in ("POST", "PUT", "PATCH") else None,
            headers=_filter_headers(request.headers.items()),
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
    try:
        upstream = requests.get(
            url,
            params=request.args,
            headers=_filter_headers(request.headers.items()),
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


# ── Feature file CRUD ────────────────────────────────────────────────────────
@agents_bp.route("/test-authoring-agent/queue", methods=["GET"])
def queue_list():
    return _forward_json("GET", "/agents/test-authoring-agent/queue")


@agents_bp.route("/test-authoring-agent/queue", methods=["POST"])
def queue_create():
    return _forward_json("POST", "/agents/test-authoring-agent/queue")


@agents_bp.route("/test-authoring-agent/queue/<name>", methods=["GET"])
def queue_read(name: str):
    return _forward_json("GET", f"/agents/test-authoring-agent/queue/{name}")


@agents_bp.route("/test-authoring-agent/config", methods=["GET"])
def authoring_config():
    return _forward_json("GET", "/agents/test-authoring-agent/config")


# ── Run control ──────────────────────────────────────────────────────────────
@agents_bp.route("/test-authoring-agent/run", methods=["POST"])
def run_start():
    return _forward_json("POST", "/agents/test-authoring-agent/run")


@agents_bp.route("/test-authoring-agent/run/active", methods=["GET"])
def run_active():
    return _forward_json("GET", "/agents/test-authoring-agent/run/active")


@agents_bp.route("/test-authoring-agent/sessions/<session_id>/events", methods=["GET"])
def authoring_session_events(session_id: str):
    return _forward_json("GET", f"/agents/test-authoring-agent/sessions/{session_id}/events")


@agents_bp.route("/test-authoring-agent/run/queue", methods=["GET"])
def pending_queue_list():
    return _forward_json("GET", "/agents/test-authoring-agent/run/queue")


@agents_bp.route("/test-authoring-agent/run/queue/<int:index>", methods=["DELETE"])
def pending_queue_remove(index: int):
    return _forward_json("DELETE", f"/agents/test-authoring-agent/run/queue/{index}")


@agents_bp.route("/test-authoring-agent/run/<session_id>/cancel", methods=["POST"])
def run_cancel(session_id: str):
    return _forward_json("POST", f"/agents/test-authoring-agent/run/{session_id}/cancel")


# ── Stream (SSE) ─────────────────────────────────────────────────────────────
@agents_bp.route("/test-authoring-agent/run/<session_id>/stream", methods=["GET"])
def run_stream(session_id: str):
    return _forward_stream(f"/agents/test-authoring-agent/run/{session_id}/stream")


# ── History ──────────────────────────────────────────────────────────────────
@agents_bp.route("/test-authoring-agent/sessions", methods=["GET"])
def sessions_list():
    return _forward_json("GET", "/agents/test-authoring-agent/sessions")


@agents_bp.route("/test-authoring-agent/sessions/<session_id>", methods=["GET"])
def sessions_get(session_id: str):
    return _forward_json("GET", f"/agents/test-authoring-agent/sessions/{session_id}")


@agents_bp.route("/test-authoring-agent/sessions/<session_id>/retry", methods=["POST"])
def sessions_retry(session_id: str):
    return _forward_json("POST", f"/agents/test-authoring-agent/sessions/{session_id}/retry")


# ── test-healing-agent ───────────────────────────────────────────────────────
# Same shapes as the authoring routes above. POST /run takes either
# {"test": "Class#method", "repair": bool, "force": bool} for a standalone run,
# or {"build_tag": "..."} to pick up a handoff test-triaging-agent already queued.
_HEALING = "/agents/test-healing-agent"


@agents_bp.route("/test-healing-agent/queue", methods=["GET"])
def healing_queue_list():
    return _forward_json("GET", f"{_HEALING}/queue")


@agents_bp.route("/test-healing-agent/queue/<name>", methods=["GET"])
def healing_queue_read(name: str):
    return _forward_json("GET", f"{_HEALING}/queue/{name}")


@agents_bp.route("/test-healing-agent/config", methods=["GET"])
def healing_config():
    return _forward_json("GET", f"{_HEALING}/config")


@agents_bp.route("/test-healing-agent/tests", methods=["GET"])
def healing_tests_list():
    return _forward_json("GET", f"{_HEALING}/tests")


@agents_bp.route("/test-healing-agent/run", methods=["POST"])
def healing_run_start():
    return _forward_json("POST", f"{_HEALING}/run")


@agents_bp.route("/test-healing-agent/run/active", methods=["GET"])
def healing_run_active():
    return _forward_json("GET", f"{_HEALING}/run/active")


@agents_bp.route("/test-healing-agent/sessions/<session_id>/events", methods=["GET"])
def healing_session_events(session_id: str):
    return _forward_json("GET", f"{_HEALING}/sessions/{session_id}/events")


@agents_bp.route("/test-healing-agent/run/queue", methods=["GET"])
def healing_pending_queue_list():
    return _forward_json("GET", f"{_HEALING}/run/queue")


@agents_bp.route("/test-healing-agent/run/queue/<int:index>", methods=["DELETE"])
def healing_pending_queue_remove(index: int):
    return _forward_json("DELETE", f"{_HEALING}/run/queue/{index}")


@agents_bp.route("/test-healing-agent/run/<session_id>/cancel", methods=["POST"])
def healing_run_cancel(session_id: str):
    return _forward_json("POST", f"{_HEALING}/run/{session_id}/cancel")


@agents_bp.route("/test-healing-agent/run/<session_id>/stream", methods=["GET"])
def healing_run_stream(session_id: str):
    return _forward_stream(f"{_HEALING}/run/{session_id}/stream")


@agents_bp.route("/test-healing-agent/sessions", methods=["GET"])
def healing_sessions_list():
    return _forward_json("GET", f"{_HEALING}/sessions")


@agents_bp.route("/test-healing-agent/sessions/<session_id>", methods=["GET"])
def healing_sessions_get(session_id: str):
    return _forward_json("GET", f"{_HEALING}/sessions/{session_id}")
