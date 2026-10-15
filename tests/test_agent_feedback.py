"""What Requirements → Tests tells QA Agent Network's feedback store
(backend/services/agent_feedback.py): what a run offered, and what a person pushed.
"""

from unittest.mock import MagicMock, patch

import pytest

from backend.services import agent_feedback

ORIGINAL = {"source": "suggested_update", "title": "Pay by card", "priority": "P1",
            "preconditions": "", "steps": "1. Add item\n2. Pay", "expected_result": "Order placed"}


@pytest.fixture
def client(app, signed_in):
    return app.test_client()


@patch("backend.api.customer.routes.RequirementAnalysisService")
def test_an_ai_written_case_pushed_after_editing_is_sent_with_both_texts(
        mock_svc_class, client, captured_agent_feedback):
    mock_svc = MagicMock()
    mock_svc.update_case_in_testrail.return_value = {"success": True, "testrail_id": "C99"}
    mock_svc_class.return_value = mock_svc
    with client.session_transaction() as session:      # what login puts there
        session["user_id"], session["username"] = "0123456789ab", "tester"
    response = client.post("/api/customer/requirement-analysis/update-case", json={
        "testrail_id": "C99", "title": "Pay by card", "priority": "P1",
        "steps": "1. Add item\n2. Pay", "expected_result": "Order placed and a receipt shown",
        "session_id": "r1", "target": {"kind": "existing", "testrail_id": "C99"},
        "ai_original": ORIGINAL})
    assert response.status_code == 200
    (sent,) = captured_agent_feedback
    payload = sent["payload"]
    assert (payload["kind"], payload["action"], payload["source"]) == \
        ("test_push", "updated", "suggested_update")
    assert payload["original"]["expected_result"] == "Order placed"
    assert payload["final"]["expected_result"] == "Order placed and a receipt shown"
    assert sent["headers"]["X-User-ID"] == "0123456789ab"
    assert sent["headers"]["X-User-Name"] == "tester"


@patch("backend.api.customer.routes.RequirementAnalysisService")
def test_a_case_a_person_wrote_themselves_sends_nothing(mock_svc_class, client,
                                                        captured_agent_feedback):
    mock_svc = MagicMock()
    mock_svc.create_case_in_testrail.return_value = {"success": True, "testrail_id": "C5"}
    mock_svc.ingest_pushed_case_into_rag.return_value = True
    mock_svc_class.return_value = mock_svc
    response = client.post("/api/customer/requirement-analysis/create-case",
                           json={"section_id": 3, "title": "Written by hand"})
    assert response.status_code == 200
    assert captured_agent_feedback == []


@patch("backend.api.customer.routes.RequirementAnalysisService")
def test_a_failed_push_sends_nothing(mock_svc_class, client, captured_agent_feedback):
    mock_svc = MagicMock()
    mock_svc.update_case_in_testrail.return_value = {"success": False, "error": "TestRail down"}
    mock_svc_class.return_value = mock_svc
    client.post("/api/customer/requirement-analysis/update-case", json={
        "testrail_id": "C99", "title": "T", "ai_original": ORIGINAL})
    assert captured_agent_feedback == []


def test_a_generation_addresses_tests_the_way_a_push_does(captured_agent_feedback):
    result = {
        "generated_tests": {"R1": [{"title": "Pay by card", "priority": "P1", "steps": "x"}]},
        "e2e_workflow_tests": [{"title": "Checkout end to end", "priority": "P0"}],
        "tests_needing_update": {"R1": [{"testrail_id": "C3", "title": "Login"}]},
    }
    assert agent_feedback.record_generation("r1", "0123456789ab", result) is True
    payload = captured_agent_feedback[0]["payload"]
    assert payload["tests"] == [
        {"target": {"kind": "generated", "req_id": "R1", "index": 0}, "title": "Pay by card",
         "priority": "P1"},
        {"target": {"kind": "e2e", "index": 0}, "title": "Checkout end to end", "priority": "P0"}]
    assert payload["updates"] == [{"req_id": "R1", "testrail_id": "C3", "title": "Login"}]
    assert captured_agent_feedback[0]["headers"]["X-User-ID"] == "0123456789ab"


def test_a_run_that_offered_nothing_sends_nothing(captured_agent_feedback):
    assert agent_feedback.record_generation("r1", "u", {"generated_tests": {}}) is False
    assert agent_feedback.record_generation("r1", "u", None) is False
    assert captured_agent_feedback == []


def test_an_unreachable_agent_server_is_one_log_line(monkeypatch, capsys):
    """The real sender, run inline: a connection error must never reach the caller."""
    import requests
    monkeypatch.undo()          # the autouse capture, for this test only
    monkeypatch.setattr(agent_feedback.threading, "Thread",
                        lambda target, **kw: type("T", (), {"start": staticmethod(target)})())

    def refuse(*args, **kwargs):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(agent_feedback.requests, "post", refuse)
    agent_feedback._post({"kind": "generation"}, {})
    assert "not recorded: connection refused" in capsys.readouterr().out


def test_lessons_are_fetched_and_their_absence_is_a_normal_run(monkeypatch):
    import requests
    monkeypatch.undo()          # the autouse stub, for this test only

    class Answer:
        status_code = 200
        body = {"mode": "on", "ids": ["L-design-001"], "block": "\n## Lessons…"}

        def json(self):
            return self.body

    monkeypatch.setattr(agent_feedback.requests, "get", lambda *a, **k: Answer())
    assert agent_feedback.fetch_lessons() == {"ids": ["L-design-001"], "block": "\n## Lessons…"}

    # Shadow: chosen but not put in the prompt, so not given to this run.
    Answer.body = {"mode": "shadow", "ids": ["L-design-001"], "block": ""}
    assert agent_feedback.fetch_lessons() == {"ids": [], "block": ""}

    def down(*args, **kwargs):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(agent_feedback.requests, "get", down)
    assert agent_feedback.fetch_lessons() == {"ids": [], "block": ""}


def test_a_generation_names_the_lessons_its_run_was_given(captured_agent_feedback):
    agent_feedback.record_generation("r1", "u", {"generated_tests": {"R1": [{"title": "A"}]},
                                                 "lessons_applied": ["L-design-001"]})
    assert captured_agent_feedback[0]["payload"]["lessons"] == ["L-design-001"]


class TestReviewOutcomesInAnalytics:
    def _answer(self, status, payload):
        body = MagicMock()
        body.get_json.return_value = payload
        return (body, status) if status != 200 else body

    def test_the_summary_is_passed_through(self, monkeypatch):
        from backend.api.admin import routes
        monkeypatch.setattr(routes, "_forward_json",
                            lambda method, path: self._answer(200, {"by_agent": {"a": {"runs": 1}}}))
        review, error = routes._review_outcomes("7d", None, None, "")
        assert review["by_agent"]["a"]["runs"] == 1 and error is None

    def test_an_older_agent_server_says_restart_it(self, monkeypatch):
        from backend.api.admin import routes
        monkeypatch.setattr(routes, "_forward_json",
                            lambda method, path: self._answer(404, {"error": "not found"}))
        review, error = routes._review_outcomes("7d", None, None, "")
        assert review is None and "restarted" in error

    def test_a_server_that_is_down_never_breaks_the_page(self, monkeypatch):
        from backend.api.admin import routes

        def down(method, path):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(routes, "_forward_json", down)
        assert routes._review_outcomes("7d", None, None, "") == (None, "connection refused")
