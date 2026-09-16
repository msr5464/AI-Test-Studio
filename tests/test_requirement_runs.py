"""Requirements -> Tests runs: recorded, replayable, cancellable, private to their user."""
import json
import threading
import time

import pytest

from backend.services import requirement_runs as runs


@pytest.fixture(autouse=True)
def sessions_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("REQUIREMENT_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("REQUIREMENT_RUNS_FILE", str(tmp_path / "requirement_runs.jsonl"))
    monkeypatch.setenv("OPERATION_COSTS_FILE", str(tmp_path / "operation_costs.jsonl"))
    return tmp_path / "sessions"


class _FakeService:
    """Stands in for RequirementAnalysisService: fires every callback, then waits to be let go."""

    def __init__(self):
        self.release = threading.Event()

    def analyze(self, *, progress_callback, requirement_result_callback, doc_summary_callback,
                requirement_step_callback, cancel_event, **_):
        progress_callback(1, "Analysing requirements", 0.05)
        doc_summary_callback({"title": "Checkout spec", "source_type": "text"})
        requirement_step_callback("REQ-1", "Finding related tests")
        while not (self.release.is_set() or cancel_event.is_set()):
            time.sleep(0.01)
        requirement_result_callback("REQ-1", {"requirement": {"id": "REQ-1", "title": "Pay"}})
        return {"success": True, "requirements_analyzed": 1, "total_estimated_cost_usd": 0.01,
                "summary": {"total_generated_tests": 3, "e2e_workflow_tests_count": 1}}


def _start(svc, user="u1"):
    return runs.start(svc, user_id=user, text="REQ-1: Pay", file_paths=[], confluence_urls=[],
                      opts={"generate_new_tests": True}, source="Pasted text")["session_id"]


def _wait_finished(sid):
    deadline = time.time() + 5
    while sid in runs._LIVE:          # leaving the registry is the last step of a run
        assert time.time() < deadline, "run did not finish"
        time.sleep(0.01)
    return runs.get_meta(sid)


def _data(frame):
    return json.loads(frame.split("data: ", 1)[1])


def test_run_streams_live_then_replays_the_same_events():
    svc = _FakeService()
    sid = _start(svc)
    assert runs.list_runs("u1")[0]["status"] == "running"

    live = runs.stream(sid, 0)
    assert next(live).startswith("id: 0\n")          # config, sent before start() returned
    svc.release.set()
    frames = list(live)                              # tails until the run ends
    assert _data(frames[-1])["status"] == "completed"

    row = _wait_finished(sid)
    assert row["status"] == "completed" and row["title"] == "Checkout spec"
    assert row["requirements"] == 1 and row["tests_generated"] == 4 and row["cost_usd"] == 0.01
    assert runs.list_runs("someone-else") == []

    events = runs.events(sid)
    assert events[0]["type"] == "config" and events[0]["generate_new_tests"] is True
    assert events[-1]["success"] is True and events[-1]["status"] == "completed"
    assert len(list(runs.stream(sid, 0))) == len(events) == len(frames) + 1
    # A reconnect resumes after the last id it saw.
    assert list(runs.stream(sid, len(events) - 1))[0].startswith(f"id: {len(events) - 1}\n")


def test_cancel_ends_the_run_as_cancelled():
    sid = _start(_FakeService())
    assert runs.cancel(sid) is True
    assert _wait_finished(sid)["status"] == "cancelled"
    assert runs.events(sid)[-1]["status"] == "cancelled"
    assert runs.cancel(sid) is False


def test_failed_analysis_keeps_its_error():
    class Boom:
        def analyze(self, **_):
            raise RuntimeError("Confluence said no")

    sid = _start(Boom())
    row = _wait_finished(sid)
    assert row["status"] == "failed" and row["error"] == "Confluence said no"
    last = runs.events(sid)[-1]
    assert (last["success"], last["error"], last["status"]) == (False, "Confluence said no", "failed")


def test_run_orphaned_by_a_restart_reads_as_interrupted(sessions_dir):
    sid = "20260916-101010-req-abcdef"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / f"{sid}.json").write_text(json.dumps(
        {"session_id": sid, "user_id": "u1", "status": "running"}))
    (sessions_dir / f"{sid}.events.jsonl").write_text('{"type": "config"}\n{"stage": 1, "mess')

    assert runs.list_runs("u1")[0]["status"] == "interrupted"
    events = runs.events(sid)
    assert events[0] == {"type": "config"}           # the truncated line is skipped
    assert events[-1]["status"] == "interrupted" and events[-1]["success"] is False


def test_ids_that_are_not_session_ids_are_refused():
    assert runs.get_meta("../../users") is None
    assert runs.events("../../users") == []


def test_analysis_console_groups_log_lines_into_phases(monkeypatch):
    """The real pipeline (LLM and retrieval mocked) runs its six steps one after
    another, and every log line belongs to the step that was open when it was written."""
    from unittest.mock import MagicMock, patch
    from backend.services.requirement_analysis_service import RequirementAnalysisService as Service

    monkeypatch.setenv("REQUIREMENT_ENRICH_WITH_CONTEXT", "false")
    rag = MagicMock()
    rag.find_related_specs.return_value = []
    rag.find_related_tests.return_value = []
    console = []
    with patch.object(Service, "_extract_acceptance_criteria", return_value=["User can log in"]), \
            patch.object(Service, "_generate_tests_for_requirement",
                         return_value=[{"title": "Log in with fingerprint", "priority": "P0"}]), \
            patch.object(Service, "_fetch_critical_product_tests", return_value=[]), \
            patch.object(Service, "_identify_e2e_workflows", return_value=[]):
        Service(rag_service=rag).analyze(text="REQ-001: User can log in with a fingerprint.",
                                         generate_new_tests=True, log_callback=console.append)

    phases = [(e["index"], e["state"]) for e in console if e["type"] == "phase"]
    assert phases == [(i, state) for i in range(1, 7) for state in ("start", "done")]
    assert [e["key"] for e in console if e["type"] == "phase" and e["state"] == "start"] == [
        "extract", "criteria", "find-tests", "coverage", "write-tests", "e2e"]
    open_phase = None
    for event in console:
        assert "ts" in event
        if event["type"] == "phase":
            open_phase = event["index"] if event["state"] == "start" else None
        else:
            assert event["phase"] == open_phase is not None, event
    text = "\n".join(e["text"] for e in console if e["type"] == "log")
    for expected in ("Requirements:", "Acceptance criteria: 1", "Related tests: none",
                     "Generated tests: 1", "Coverage:", "Existing E2E tests"):
        assert expected in text, expected


def test_testrail_pushes_are_recorded_on_their_run_for_its_owner_only():
    svc = _FakeService()
    sid = _start(svc)
    # While the run is live the push goes through the run itself, so live viewers see it.
    assert runs.record_push(sid, "u1", "created", "C9001", "Log in with fingerprint",
                            {"kind": "generated", "req_id": "REQ-1", "index": "0"}) is True
    svc.release.set()
    _wait_finished(sid)
    # Once finished it is appended to the recorded events directly.
    assert runs.record_push(sid, "u1", "updated", "C101", "Login", {"kind": "existing", "testrail_id": "C101"}) is True
    assert runs.record_push(sid, "someone-else", "created", "C9002", "x", {"kind": "e2e", "index": 0}) is False
    assert runs.record_push(sid, "u1", "created", "C9003", "x", {"kind": "e2e"}) is False          # no index
    assert runs.record_push("../../users", "u1", "created", "C9004", "x", {"kind": "e2e", "index": 0}) is False

    pushes = [{k: e[k] for k in ("action", "testrail_id", "target")}
              for e in runs.events(sid) if e.get("type") == "testrail_push"]
    assert pushes == [
        {"action": "created", "testrail_id": "C9001", "target": {"kind": "generated", "req_id": "REQ-1", "index": 0}},
        {"action": "updated", "testrail_id": "C101", "target": {"kind": "existing", "testrail_id": "C101"}},
    ]
