"""Tell QA Agent Network what Requirements → Tests produced, and what a person did with it.

The agents' feedback store lives in QA Agent Network (shared/feedback.py there), so
that every agent's feedback is in one place, Requirements → Tests included: it is
`test-design-agent` there, as on the Analytics page. Two things are sent:

  * **generation** — when a run completes, which tests it offered. Nothing else
    can later say which of them a person never pushed.
  * **test_push** — when a person pushes AI-written test text to TestRail, the text
    the AI wrote beside the text that was pushed. Whether, and how, a person edited
    it is the most useful signal Requirements → Tests produces.

Best-effort and off the request path: the call runs in a background thread with a
short timeout, and a failure is one log line. A TestRail push must never wait on,
or fail because of, the agent server.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Dict, Iterable, Optional

import requests

_PATH = "/feedback/test-design"
_TIMEOUT_S = 5
CASE_FIELDS = ("title", "priority", "preconditions", "steps", "expected_result")


def _upstream() -> str:
    return os.getenv("QA_AGENT_NETWORK_URL", "http://localhost:6001").rstrip("/")


def _headers(user_id: Optional[str] = None) -> Dict[str, str]:
    """The identity headers the agent server trusts, as the agents proxy sends them.

    Inside a request they come from the signed-in session, through the proxy's own
    injector. A background run has no request, so it names its user directly.
    """
    if user_id is None:
        from backend.api.agents.proxy import _inject_user_headers
        return _inject_user_headers({})
    headers = {"X-User-ID": user_id, "X-User-Role": "member"}
    secret = (os.getenv("QA_AGENT_PROXY_SECRET") or "").strip()
    if secret:
        headers["X-Proxy-Secret"] = secret
    return headers


def _post(payload: Dict[str, Any], headers: Dict[str, str]) -> None:
    def send() -> None:
        try:
            response = requests.post(_upstream() + _PATH, json=payload, headers=headers,
                                     timeout=_TIMEOUT_S)
            if response.status_code >= 300:
                print(f"[agent-feedback] {payload.get('kind')} not recorded: "
                      f"HTTP {response.status_code} {response.text[:200]}")
        except requests.RequestException as e:
            print(f"[agent-feedback] {payload.get('kind')} not recorded: {e}")
    threading.Thread(target=send, name="agent-feedback", daemon=True).start()


def case_fields(source: Optional[Dict[str, Any]]) -> Dict[str, str]:
    source = source or {}
    return {name: str(source.get(name) or "") for name in CASE_FIELDS}


def record_push(data: Dict[str, Any], action: str, testrail_id: Any) -> bool:
    """A push the page says was AI-written (`ai_original` in its body). Returns whether sent.

    A case a person wrote from scratch carries no `ai_original`, and says nothing
    about test design.
    """
    original = data.get("ai_original")
    if not isinstance(original, dict):
        return False
    try:
        headers = _headers()
    except Exception as e:      # no request context, or no auth service: say so, never raise
        print(f"[agent-feedback] test_push not recorded: {e}")
        return False
    _post({"kind": "test_push", "session": data.get("session_id") or "",
           "action": action, "testrail_id": str(testrail_id or ""),
           "source": str(original.get("source") or "generated"),
           "target": data.get("target"), "original": case_fields(original),
           "final": case_fields(data)}, headers)
    return True


def _tests(result: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    """Every test a run offered, addressed the way a push names its `target`."""
    for req_id, tests in (result.get("generated_tests") or {}).items():
        for index, test in enumerate(tests or []):
            yield {"target": {"kind": "generated", "req_id": req_id, "index": index},
                   "title": test.get("title"), "priority": test.get("priority")}
    for index, test in enumerate(result.get("e2e_workflow_tests") or []):
        yield {"target": {"kind": "e2e", "index": index},
               "title": test.get("title"), "priority": test.get("priority")}


def _updates(result: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    for req_id, tests in (result.get("tests_needing_update") or {}).items():
        for test in tests or []:
            yield {"req_id": req_id,
                   "testrail_id": test.get("testrail_id") or test.get("id") or "",
                   "title": test.get("title")}


def record_generation(session_id: str, user_id: str, result: Any) -> bool:
    """What a completed run offered. Returns whether anything was sent."""
    if not isinstance(result, dict):
        return False
    tests, updates = list(_tests(result)), list(_updates(result))
    if not tests and not updates:
        return False
    _post({"kind": "generation", "session": session_id, "tests": tests, "updates": updates,
           "lessons": list(result.get("lessons_applied") or [])}, _headers(user_id))
    return True


def fetch_lessons(timeout_s: float = 3.0) -> Dict[str, Any]:
    """Lessons for Requirements → Tests from QA Agent Network: {"ids", "block"}.

    `block` is the text to add to generation prompts, empty unless lessons are on
    there. `ids` are the lessons that text carries: the run records them as given,
    so in shadow mode, where nothing reaches the prompt, there are none. Never
    raises and never waits long: a run without lessons is a normal run.
    """
    try:
        response = requests.get(f"{_upstream()}/lessons/test-design-agent",
                                params={"step": "generate"}, timeout=timeout_s)
        if response.status_code == 200:
            body = response.json()
            block = body.get("block") or ""
            return {"ids": list(body.get("ids") or []) if block else [], "block": block}
    except (requests.RequestException, ValueError):
        pass
    return {"ids": [], "block": ""}
