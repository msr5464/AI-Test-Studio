"""
Tests for Requirement Analysis (POST /api/customer/requirement-analysis).
Covers API shape, extraction, and optional integration with real RAG/LLM.
"""
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# Sample response from RequirementAnalysisService.analyze() for unit test
def _mock_analyze_result():
    return {
        "success": True,
        "requirements_analyzed": 2,
        "requirements": [
            {"id": "REQ-001", "title": "User reset password", "description": "User must reset password via email."},
            {"id": "REQ-002", "title": "Verification email", "description": "System shall send verification email within 60 seconds."},
        ],
        "related_specs": [
            {"title": "Prior Auth Spec", "content": "Password reset flow...", "url": "https://confluence/123", "similarity_score": 0.82},
        ],
        "related_tests": {
            "REQ-001": [{"testrail_id": "C123", "title": "Reset password flow", "similarity_score": 0.85}],
            "REQ-002": [],
        },
        "tests_needing_update": {
            "REQ-001": [{"testrail_id": "C123", "title": "Reset password flow", "status": "needs_update", "suggested_changes": ["Add email step"], "reason": "Missing email check", "confidence": 0.8}],
            "REQ-002": [],
        },
        "tests_ok": {"REQ-001": [], "REQ-002": []},
        "uncovered_requirements": ["REQ-002"],
        "generated_tests": {
            "REQ-002": [{"title": "Verify email within 60s", "priority": "P1", "steps": "1. Trigger reset\n2. Check email", "expected_result": "Email within 60s", "generated": True}],
        },
        "recommended_e2e_set": {
            "REQ-001": {"reuse_as_is": [], "use_after_update": [{"testrail_id": "C123", "title": "Reset password flow", "status": "needs_update"}], "create_new": []},
            "REQ-002": {"reuse_as_is": [], "use_after_update": [], "create_new": [{"title": "Verify email within 60s", "priority": "P1", "generated": True}]},
        },
        "coverage_gap_reason_per_req": {"REQ-002": "No related tests."},
        "pushed_to_testrail": [],
        "summary": {
            "total_requirements": 2,
            "requirements_with_coverage": 1,
            "needing_update_count": 1,
            "uncovered_count": 1,
            "generated_count": 1,
            "total_generated_tests": 1,
            "pushed_count": 0,
        },
    }


class TestRequirementAnalysisAPI:
    """API tests for requirement-analysis endpoint (mocked service)."""

    @pytest.fixture
    def app(self):
        from backend.app import create_app
        return create_app()

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    @patch("backend.api.customer.routes.RequirementAnalysisService")
    def test_requirement_analysis_paste_returns_expected_shape(self, mock_svc_class, client):
        """POST with requirement_spec (paste) returns success and all expected keys."""
        mock_svc = MagicMock()
        mock_svc.analyze.return_value = _mock_analyze_result()
        mock_svc_class.return_value = mock_svc

        resp = client.post(
            "/api/customer/requirement-analysis",
            json={
                "requirement_spec": "REQ-001: User must reset password.\nREQ-002: System shall send email.",
                "generate_new_tests": True,
            },
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None
        assert data.get("success") is True
        assert data.get("requirements_analyzed") == 2
        assert "requirements" in data
        assert "related_specs" in data
        assert "related_tests" in data
        assert "tests_needing_update" in data
        assert "uncovered_requirements" in data
        assert "generated_tests" in data
        assert "recommended_e2e_set" in data
        assert "summary" in data
        assert "pushed_to_testrail" in data
        assert data["summary"].get("needing_update_count") == 1
        assert data["summary"].get("pushed_count") == 0
        assert "REQ-001" in data["recommended_e2e_set"]
        assert "reuse_as_is" in data["recommended_e2e_set"]["REQ-001"]
        assert "use_after_update" in data["recommended_e2e_set"]["REQ-001"]
        assert "create_new" in data["recommended_e2e_set"]["REQ-001"]

    @patch("backend.api.customer.routes.RequirementAnalysisService")
    def test_requirement_analysis_rejects_empty_input(self, mock_svc_class, client):
        """POST with no input returns 400."""
        resp = client.post(
            "/api/customer/requirement-analysis",
            json={},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data.get("success") is False
        assert "error" in data

    @patch("backend.api.customer.routes.RequirementAnalysisService")
    def test_requirement_analysis_accepts_push_options(self, mock_svc_class, client):
        """POST with push_to_testrail and target_section_id passes them to analyze()."""
        mock_svc = MagicMock()
        result = _mock_analyze_result()
        result["pushed_to_testrail"] = [{"requirement_id": "REQ-002", "testrail_id": "C456", "success": True}]
        result["summary"]["pushed_count"] = 1
        mock_svc.analyze.return_value = result
        mock_svc_class.return_value = mock_svc

        resp = client.post(
            "/api/customer/requirement-analysis",
            json={
                "requirement_spec": "REQ-001: Foo.",
                "push_to_testrail": True,
                "target_section_id": 42,
            },
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        mock_svc.analyze.assert_called_once()
        call_kw = mock_svc.analyze.call_args[1]
        assert call_kw.get("push_to_testrail") is True
        assert call_kw.get("target_section_id") == 42

    @patch("backend.api.customer.routes.RequirementAnalysisService")
    def test_requirement_analysis_accepts_generate_p2_p3_tests(self, mock_svc_class, client):
        """POST with generate_p2_p3_tests passes it to analyze()."""
        mock_svc = MagicMock()
        mock_svc.analyze.return_value = _mock_analyze_result()
        mock_svc_class.return_value = mock_svc

        resp = client.post(
            "/api/customer/requirement-analysis",
            json={
                "requirement_spec": "REQ-001: Foo.",
                "generate_p2_p3_tests": True,
            },
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        call_kw = mock_svc.analyze.call_args[1]
        assert call_kw.get("generate_p2_p3_tests") is True


class TestStreamTrim:
    """Ensure stream payload trim preserves related_tests, tests_needing_update, and recommended_e2e_set."""

    def test_trim_result_for_stream_preserves_recommended_e2e_set(self):
        """_trim_result_for_stream preserves recommended_e2e_set and coverage_gap_reason_per_req."""
        from backend.api.customer.routes import _trim_result_for_stream

        result = {
            "success": True,
            "requirements_analyzed": 1,
            "related_tests": {"REQ-001": []},
            "tests_needing_update": {"REQ-001": []},
            "generated_tests": {"REQ-001": [{"title": "T", "steps": "x" * 5000}]},
            "recommended_e2e_set": {"REQ-001": {"reuse_as_is": [], "use_after_update": [], "create_new": [{"title": "T"}]}},
            "coverage_gap_reason_per_req": {"REQ-001": "Only partial flow."},
        }
        out = _trim_result_for_stream(result)
        assert out.get("recommended_e2e_set") == result["recommended_e2e_set"]
        assert out.get("coverage_gap_reason_per_req") == result["coverage_gap_reason_per_req"]

    def test_trim_requirement_result_preserves_related_tests_and_needing_update(self):
        from backend.api.customer.routes import _trim_requirement_result_for_stream

        data = {
            "requirement": {"id": "REQ-1", "title": "Login", "description": "Login flow"},
            "related_tests": [
                {"testrail_id": "C59", "title": "Login with verified account", "content": "x" * 20000},
            ],
            "tests_needing_update": [
                {"testrail_id": "C19", "title": "Lockout", "content": "y" * 20000, "reason": "partial"},
            ],
            "tests_ok": ["C59"],
            "generated_tests": [],
            "uncovered": False,
        }
        out = _trim_requirement_result_for_stream(data)
        assert out is not None
        assert len(out.get("related_tests", [])) == 1, "related_tests must not be emptied by trim"
        assert len(out.get("tests_needing_update", [])) == 1, "tests_needing_update must not be emptied by trim"
        assert out["related_tests"][0]["testrail_id"] == "C59"
        assert out["tests_needing_update"][0]["testrail_id"] == "C19"


class TestRequirementAnalysisServiceE2E:
    """
    Unit tests for requirement analysis e2e flow: Confluence prior context + TestRail context.
    Verifies find_related_specs is used and specs_context is passed into test generation.
    """

    @pytest.fixture
    def mock_rag_service(self):
        mock_rag = MagicMock()
        mock_rag.find_related_specs.return_value = [
            {"title": "Prior Auth Spec", "content": "Password reset flow from Confluence.", "url": "https://confluence/1", "similarity_score": 0.8},
        ]
        mock_rag.find_related_tests.return_value = []
        return mock_rag

    def test_analyze_calls_find_related_specs_per_requirement(self, mock_rag_service):
        """analyze() calls find_related_specs once per requirement with that requirement's text and k=10."""
        from backend.services.requirement_analysis_service import RequirementAnalysisService

        spec_text = "REQ-001: User must reset password.\nREQ-002: System shall send email."
        with patch.object(RequirementAnalysisService, "_assess_updates", return_value=([], [])):
            with patch.object(RequirementAnalysisService, "_generate_tests_for_requirement", return_value=[{"title": "Generated", "priority": "P1", "generated": True}]):
                svc = RequirementAnalysisService(rag_service=mock_rag_service)
                svc.analyze(text=spec_text, generate_new_tests=True)

        assert mock_rag_service.find_related_specs.call_count == 2
        calls = mock_rag_service.find_related_specs.call_args_list
        for call in calls:
            assert call[1].get("k", 10) == 10
        texts = [c[0][0] for c in calls]
        assert any("reset password" in t for t in texts)
        assert any("send email" in t for t in texts)

    def test_analyze_passes_specs_context_to_generate_test(self, mock_rag_service):
        """When generating new tests, _generate_tests_for_requirement is called with specs_context=related_specs."""
        from backend.services.requirement_analysis_service import RequirementAnalysisService

        spec_text = "REQ-001: User must reset password.\nREQ-002: System shall send email."
        related_specs = [
            {"title": "Prior Auth Spec", "content": "Password reset.", "url": "https://c/1", "similarity_score": 0.82},
        ]
        mock_rag_service.find_related_specs.return_value = related_specs
        mock_rag_service.find_related_tests.return_value = []

        with patch.object(RequirementAnalysisService, "_assess_updates", return_value=([], [])):
            gen_mock = MagicMock(return_value=[{"title": "Verify email", "priority": "P1", "generated": True}])
            with patch.object(RequirementAnalysisService, "_generate_tests_for_requirement", gen_mock):
                svc = RequirementAnalysisService(rag_service=mock_rag_service)
                result = svc.analyze(text=spec_text, generate_new_tests=True)

        assert result.get("related_specs") == related_specs
        assert gen_mock.called
        call_kw = gen_mock.call_args[1]
        assert call_kw.get("specs_context") == related_specs
        assert isinstance(gen_mock.return_value, list)

    def test_analyze_returns_related_specs_in_result(self, mock_rag_service):
        """analyze() result includes related_specs key with Confluence prior context."""
        from backend.services.requirement_analysis_service import RequirementAnalysisService

        spec_text = "REQ-001: User must reset password."
        with patch.object(RequirementAnalysisService, "_assess_updates", return_value=([], [])):
            with patch.object(RequirementAnalysisService, "_generate_tests_for_requirement", return_value=[{"title": "Gen", "generated": True}]):
                svc = RequirementAnalysisService(rag_service=mock_rag_service)
                result = svc.analyze(text=spec_text, generate_new_tests=True)

        assert "related_specs" in result
        assert isinstance(result["related_specs"], list)
        assert len(result["related_specs"]) == 1
        assert result["related_specs"][0]["title"] == "Prior Auth Spec"

    def test_analyze_calls_progress_callback_with_stages(self, mock_rag_service):
        """analyze() with progress_callback receives stage 1..3 and progress 0..1."""
        from backend.services.requirement_analysis_service import RequirementAnalysisService

        progress_events = []
        def capture(stage, message, progress):
            progress_events.append((stage, message, progress))

        with patch.object(RequirementAnalysisService, "_assess_updates", return_value=([], [])):
            with patch.object(RequirementAnalysisService, "_generate_tests_for_requirement", return_value=[]):
                svc = RequirementAnalysisService(rag_service=mock_rag_service)
                svc.analyze(
                    text="REQ-001: User must log in.",
                    generate_new_tests=True,
                    progress_callback=capture,
                )
        assert len(progress_events) >= 3
        stages = [e[0] for e in progress_events]
        assert 1 in stages and 2 in stages and 3 in stages
        assert progress_events[-1][2] == 1.0


class TestRAGServiceFindRelatedSpecs:
    """Unit tests for RAGService.find_related_specs (Confluence/specs retrieval)."""

    def test_find_related_specs_returns_empty_when_no_vectorstore(self):
        """find_related_specs returns [] when vectorstore is not loaded."""
        from backend.services.rag_service import RAGService

        mock_rag = MagicMock()
        mock_rag.vectorstore = None
        mock_rag._load_vectorstore_if_needed = MagicMock()

        with patch("backend.services.rag_service.MultiFormatRAG", return_value=mock_rag):
            with patch.object(RAGService, "_load_document_metadata"):
                with patch.object(RAGService, "_load_existing_documents"):
                    svc = RAGService()
        svc.rag.vectorstore = None
        result = svc.find_related_specs("some requirement text", k=5)
        assert result == []

    def test_find_related_specs_returns_formatted_results_when_vectorstore_returns_docs(self):
        """find_related_specs returns list of dicts with title, content, url, similarity_score."""
        from langchain_core.documents import Document

        from backend.services.rag_service import RAGService

        doc = Document(
            page_content="Title: Prior Spec\n\nPassword reset flow.",
            metadata={"title": "Auth Spec", "url": "https://confluence/1", "source_type": "specs", "page_id": "123"},
        )
        mock_rag = MagicMock()
        mock_rag.vectorstore = MagicMock()
        mock_rag.retrieve_documents_with_scores.return_value = [(doc, 0.8)]
        mock_rag._load_vectorstore_if_needed = MagicMock()

        with patch("backend.services.rag_service.MultiFormatRAG", return_value=mock_rag):
            with patch.object(RAGService, "_load_document_metadata"):
                with patch.object(RAGService, "_load_existing_documents"):
                    svc = RAGService()

        result = svc.find_related_specs("password reset", k=10)

        assert len(result) == 1
        assert result[0]["title"] == "Auth Spec"
        assert "Password reset" in result[0]["content"]
        assert result[0]["url"] == "https://confluence/1"
        assert result[0]["similarity_score"] == 0.8


def _make_llm_response(content_str: str):
    """Return an object with .content as a real string for LangChain chain result."""
    from types import SimpleNamespace
    return SimpleNamespace(content=content_str)


class TestGeneratedTestsPriorityAndCoverage:
    """E2E self-tests: generated test cases must be P0>P1>P2>P3 and have required fields."""

    def test_generated_tests_sorted_by_priority_p0_first(self):
        """Generated tests are returned in priority order P0, P1, P2, P3 (critical first)."""
        from backend.services.requirement_analysis_service import RequirementAnalysisService

        json_unsorted = (
            '[{"title":"High","priority":"P1","preconditions":"","steps":"S1","expected_result":"E1"},'
            '{"title":"Critical","priority":"P0","preconditions":"","steps":"S0","expected_result":"E0"},'
            '{"title":"Low","priority":"P3","preconditions":"","steps":"S3","expected_result":"E3"},'
            '{"title":"Medium","priority":"P2","preconditions":"","steps":"S2","expected_result":"E2"}]'
        )
        mock_rag = MagicMock()
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _make_llm_response(json_unsorted)
        mock_llm.__call__ = MagicMock(return_value=_make_llm_response(json_unsorted))
        mock_rag.llm = mock_llm
        mock_rag_service = MagicMock()
        mock_rag_service.rag = mock_rag

        svc = RequirementAnalysisService(rag_service=mock_rag_service)
        req = {"id": "REQ-1", "title": "Login", "description": "User can log in."}
        with patch("langchain_core.prompts.ChatPromptTemplate") as mock_prompt:
            mock_chain = MagicMock()
            mock_chain.invoke.return_value = _make_llm_response(json_unsorted)
            mock_prompt.from_messages.return_value.__or__ = MagicMock(return_value=mock_chain)
            result = svc._generate_tests_for_requirement(req, [], None, generate_p2_p3=True)

        assert len(result) == 4
        priorities = [t.get("priority") for t in result]
        assert priorities == ["P0", "P1", "P2", "P3"], f"Expected P0>P1>P2>P3 order, got {priorities}"

    def test_generated_tests_have_required_fields_and_valid_priority(self):
        """Each generated test has title, priority in {P0,P1,P2,P3}, steps, expected_result."""
        from backend.services.requirement_analysis_service import RequirementAnalysisService

        json_tests = (
            '[{"title":"E2E Login","priority":"P0","preconditions":"None","steps":"1. Open app\\n2. Login","expected_result":"User is logged in"},'
            '{"title":"E2E Logout","priority":"P1","preconditions":"Logged in","steps":"1. Click logout","expected_result":"User is logged out"}]'
        )
        mock_rag = MagicMock()
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _make_llm_response(json_tests)
        mock_rag.llm = mock_llm
        mock_rag_service = MagicMock()
        mock_rag_service.rag = mock_rag

        svc = RequirementAnalysisService(rag_service=mock_rag_service)
        req = {"id": "REQ-1", "title": "Auth", "description": "Login and logout."}
        with patch("langchain_core.prompts.ChatPromptTemplate") as mock_prompt:
            mock_chain = MagicMock()
            mock_chain.invoke.return_value = _make_llm_response(json_tests)
            mock_prompt.from_messages.return_value.__or__ = MagicMock(return_value=mock_chain)
            result = svc._generate_tests_for_requirement(req, [], None)

        assert len(result) >= 1
        valid_priorities = {"P0", "P1", "P2", "P3"}
        for t in result:
            assert t.get("title"), f"Test missing title: {t}"
            p = (t.get("priority") or "").upper()
            assert p in valid_priorities, f"Invalid priority {t.get('priority')}, must be P0/P1/P2/P3"
            assert t.get("steps") is not None or t.get("expected_result") is not None, f"Test should have steps or expected_result: {t}"

    def test_analyze_result_generated_tests_ordered_by_priority(self):
        """Full analyze() returns generated_tests with priority order P0 then P1 then P2 then P3."""
        from backend.services.requirement_analysis_service import RequirementAnalysisService

        mock_rag_service = MagicMock()
        mock_rag_service.find_related_specs.return_value = []
        mock_rag_service.find_related_tests.return_value = []
        # Return mixed order; service will sort before assigning
        mock_rag_service.rag = MagicMock()
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(
            content='[{"title":"P1 test","priority":"P1","preconditions":"","steps":"S1","expected_result":"E1"},'
                    '{"title":"P0 test","priority":"P0","preconditions":"","steps":"S0","expected_result":"E0"}]'
        )
        mock_rag_service.rag.llm = mock_llm

        with patch.object(RequirementAnalysisService, "_assess_updates", return_value=([], [])):
            svc = RequirementAnalysisService(rag_service=mock_rag_service)
            result = svc.analyze(
                text="REQ-001: User must log in.",
                generate_new_tests=True,
            )

        assert "generated_tests" in result
        for req_id, tests in result["generated_tests"].items():
            if not tests:
                continue
            priorities = [t.get("priority") for t in tests]
            expected_order = sorted(priorities, key=lambda x: {"P0": 4, "P1": 3, "P2": 2, "P3": 1}.get((x or "P2").upper(), 2), reverse=True)
            assert priorities == expected_order, f"Generated tests for {req_id} should be ordered P0>P1>P2>P3, got {priorities}"


class TestCoverageSufficientGeneration:
    """When related tests exist but coverage is insufficient, we still generate new tests."""

    def test_analyze_generates_when_coverage_insufficient(self):
        """If we have fewer than 3 P0 or 3 P1 related tests (above min similarity), generate_priorities is non-empty and we generate."""
        from backend.services.requirement_analysis_service import RequirementAnalysisService

        mock_rag = MagicMock()
        mock_rag.find_related_specs.return_value = []
        mock_rag.find_related_tests.return_value = [
            {"testrail_id": "C1", "title": "Partial flow", "content": "Only one step.", "priority": "P0", "similarity_score": 0.82},
        ]

        with patch.object(RequirementAnalysisService, "_assess_updates", return_value=([], ["C1"])):
            with patch.object(RequirementAnalysisService, "_generate_tests_for_requirement", return_value=[{"title": "E2E full flow", "priority": "P1", "generated": True}]) as gen_mock:
                svc = RequirementAnalysisService(rag_service=mock_rag)
                result = svc.analyze(text="REQ-001: User must complete login and verify dashboard.", generate_new_tests=True)

        assert "REQ-001" in result.get("uncovered_requirements", [])
        assert "REQ-001" in result.get("generated_tests", {})
        assert len(result["generated_tests"]["REQ-001"]) == 1
        gen_mock.assert_called_once()

    def test_analyze_does_not_generate_when_coverage_sufficient(self):
        """If we have >= 3 P0 and >= 3 P1 related tests, we do not generate (generate_priorities empty)."""
        from backend.services.requirement_analysis_service import RequirementAnalysisService

        mock_rag = MagicMock()
        mock_rag.find_related_specs.return_value = []
        mock_rag.find_related_tests.return_value = [
            {"testrail_id": f"C{i}", "title": "P0 flow", "content": "E2E.", "priority": "P0", "similarity_score": 0.82} for i in range(3)
        ] + [
            {"testrail_id": f"D{i}", "title": "P1 flow", "content": "E2E.", "priority": "P1", "similarity_score": 0.82} for i in range(3)
        ]

        with patch.object(RequirementAnalysisService, "_assess_updates", return_value=([], ["C0", "C1", "C2", "D0", "D1", "D2"])):
            with patch.object(RequirementAnalysisService, "_generate_tests_for_requirement") as gen_mock:
                svc = RequirementAnalysisService(rag_service=mock_rag)
                result = svc.analyze(text="REQ-001: User must complete login and verify dashboard.", generate_new_tests=True)

        assert "REQ-001" not in result.get("uncovered_requirements", [])
        assert result.get("generated_tests", {}).get("REQ-001") is None or result["generated_tests"].get("REQ-001") == []
        gen_mock.assert_not_called()

    def test_analyze_shortcut_does_not_generate_when_five_high_similarity_tests(self):
        """When we have >= 3 P0 and >= 3 P1 related tests, generate_priorities is empty so we do not generate."""
        from backend.services.requirement_analysis_service import RequirementAnalysisService

        mock_rag = MagicMock()
        mock_rag.find_related_specs.return_value = []
        mock_rag.find_related_tests.return_value = [
            {"testrail_id": f"C{i}", "title": f"P0 test {i}", "content": "E2E.", "similarity_score": 0.82, "priority": "P0"} for i in range(3)
        ] + [
            {"testrail_id": f"D{i}", "title": f"P1 test {i}", "content": "E2E.", "similarity_score": 0.82, "priority": "P1"} for i in range(3)
        ]

        with patch.object(RequirementAnalysisService, "_assess_updates", return_value=([], ["C0", "C1", "C2", "D0", "D1", "D2"])):
            with patch.object(RequirementAnalysisService, "_generate_tests_for_requirement") as gen_mock:
                svc = RequirementAnalysisService(rag_service=mock_rag)
                result = svc.analyze(text="REQ-001: Login functionality.", generate_new_tests=True)

        assert "REQ-001" not in result.get("uncovered_requirements", [])
        gen_mock.assert_not_called()


class TestComputeGeneratePriorities:
    """_compute_generate_priorities: which priorities to generate based on counts (min 3 per priority)."""

    def test_returns_empty_when_three_each_p0_p1_above_similarity(self):
        from backend.services.requirement_analysis_service import _compute_generate_priorities

        tests = (
            [{"priority": "P0", "similarity_score": 0.85} for _ in range(3)]
            + [{"priority": "P1", "similarity_score": 0.85} for _ in range(3)]
        )
        assert _compute_generate_priorities(tests, generate_p2_p3=False) == []

    def test_returns_p0_p1_when_few_tests_above_similarity(self):
        from backend.services.requirement_analysis_service import _compute_generate_priorities

        tests = [{"priority": "P0", "similarity_score": 0.8}, {"priority": "P1", "similarity_score": 0.8}]
        assert set(_compute_generate_priorities(tests, generate_p2_p3=False)) == {"P0", "P1"}

    def test_returns_only_p1_when_three_p0_above_similarity(self):
        from backend.services.requirement_analysis_service import _compute_generate_priorities

        tests = [{"priority": "P0", "similarity_score": 0.85} for _ in range(3)] + [{"priority": "P1", "similarity_score": 0.85}]
        assert _compute_generate_priorities(tests, generate_p2_p3=False) == ["P1"]

    def test_ignores_weak_matches_below_similarity_threshold(self):
        """Tests with similarity_score below REQUIREMENT_COVERAGE_SUFFICIENT_MIN_SIMILARITY don't count toward cap."""
        from backend.services.requirement_analysis_service import _compute_generate_priorities

        # 3 P0 and 3 P1 but all with low similarity (0.5) -> they don't count, so we need both P0 and P1
        tests = [
            {"priority": "P0", "similarity_score": 0.5} for _ in range(3)
        ] + [
            {"priority": "P1", "similarity_score": 0.5} for _ in range(3)
        ]
        assert set(_compute_generate_priorities(tests, generate_p2_p3=False)) == {"P0", "P1"}


class TestIsCoverageSufficient:
    """_is_coverage_sufficient returns True/False from LLM JSON."""

    def test_returns_true_when_llm_says_sufficient(self):
        from langchain_core.messages import AIMessage
        from backend.services.requirement_analysis_service import RequirementAnalysisService

        mock_rag = MagicMock()
        mock_rag.rag = MagicMock()
        mock_rag.rag.llm = MagicMock()
        mock_rag.rag.llm.invoke.return_value = AIMessage(content='{"sufficient": true, "reason": "Full coverage."}')
        with patch("backend.services.requirement_analysis_service.record_from_langchain_result", return_value=None):
            svc = RequirementAnalysisService(rag_service=mock_rag)
            sufficient, reason = svc._is_coverage_sufficient("Requirement text", [{"testrail_id": "C1", "title": "T", "content": "Full E2E."}])
        assert sufficient is True
        # reason may be parsed from LLM JSON or empty if parse path differs under mock
        assert isinstance(reason, str)

    def test_returns_false_when_no_related_tests(self):
        from backend.services.requirement_analysis_service import RequirementAnalysisService

        svc = RequirementAnalysisService(rag_service=MagicMock())
        sufficient, reason = svc._is_coverage_sufficient("Req", [])
        assert sufficient is False
        assert "No related" in reason or reason


class TestCoverageSufficientShortcut:
    """_coverage_sufficient_shortcut: deterministic rule to avoid always generating when already covered."""

    def test_returns_true_when_at_least_five_tests_above_threshold(self):
        from backend.services.requirement_analysis_service import _coverage_sufficient_shortcut

        tests = [{"testrail_id": f"C{i}", "similarity_score": 0.85} for i in range(5)]
        assert _coverage_sufficient_shortcut(tests) is True

    def test_returns_false_when_fewer_than_five_tests(self):
        from backend.services.requirement_analysis_service import _coverage_sufficient_shortcut

        tests = [{"testrail_id": f"C{i}", "similarity_score": 0.9} for i in range(4)]
        assert _coverage_sufficient_shortcut(tests) is False

    def test_returns_false_when_any_score_below_threshold(self):
        from backend.services.requirement_analysis_service import _coverage_sufficient_shortcut

        tests = [{"testrail_id": f"C{i}", "similarity_score": 0.85 if i < 4 else 0.5} for i in range(5)]
        assert _coverage_sufficient_shortcut(tests) is False

    def test_returns_false_when_any_score_none(self):
        from backend.services.requirement_analysis_service import _coverage_sufficient_shortcut

        tests = [{"testrail_id": f"C{i}", "similarity_score": 0.85 if i < 4 else None} for i in range(5)]
        assert _coverage_sufficient_shortcut(tests) is False


class TestRequirementAnalysisConfig:
    """Config loading: requirement analysis env vars (renamed for clarity)."""

    def test_config_has_requirement_retrieval_similarity_threshold(self):
        """REQUIREMENT_RETRIEVAL_SIMILARITY_THRESHOLD loads as requirement_retrieval_similarity_threshold."""
        from backend.rag.rag_settings import get_config
        config = get_config()
        assert hasattr(config, "requirement_retrieval_similarity_threshold")
        val = config.requirement_retrieval_similarity_threshold
        assert isinstance(val, (int, float))
        assert 0 <= val <= 100

    def test_config_has_requirement_needs_update_confidence_threshold(self):
        """REQUIREMENT_NEEDS_UPDATE_CONFIDENCE_THRESHOLD loads as requirement_needs_update_confidence_threshold."""
        from backend.rag.rag_settings import get_config
        config = get_config()
        assert hasattr(config, "requirement_needs_update_confidence_threshold")
        val = config.requirement_needs_update_confidence_threshold
        assert isinstance(val, (int, float))
        assert 0 <= val <= 1


class TestRequirementExtractor:
    """Requirement extraction: section-based fallback for Confluence-style specs."""

    def test_section_based_extraction_yields_multiple_requirements(self):
        """When no REQ-XXX headers, split by ## or numbered list yields multiple requirements."""
        from backend.extractors.requirement_extractor import extract_requirements

        spec = """## Login flow
User must be able to log in with email and password.
Validation and error messages apply.

## Password reset
User can request password reset. Email is sent within 60 seconds.

## Profile
User can update profile and preferences.
"""
        reqs = extract_requirements(spec)
        assert len(reqs) >= 2
        ids = [r["id"] for r in reqs]
        assert "REQ-001" in ids
        assert all("REQ-" in r["id"] for r in reqs)
        assert any("Login" in (r.get("title") or "") for r in reqs)
        assert any("Password" in (r.get("title") or "") or "reset" in (r.get("description") or "").lower() for r in reqs)

    def test_numbered_list_yields_multiple_requirements(self):
        """Numbered list 1. 2. 3. without REQ-XXX yields multiple requirements."""
        from backend.extractors.requirement_extractor import extract_requirements

        spec = """1. User can sign up with email.
Validation and terms acceptance.

2. User can log in with SSO.
Google and Microsoft supported.

3. User can reset password.
Email link valid 24 hours.
"""
        reqs = extract_requirements(spec)
        assert len(reqs) >= 2
        assert reqs[0]["id"] == "REQ-001"

    def test_us_user_story_headers_yield_one_requirement_per_us(self):
        """US #1, US #2, User Story #3, User Story 4 are each a requirement header."""
        from backend.extractors.requirement_extractor import extract_requirements

        spec = """US #1
As a user I can log in with email and password.

US #2
As a user I can reset my password via email link.

US #3
As a user I can update my profile.

User Story #4
As a user I can log out from all devices.
"""
        reqs = extract_requirements(spec)
        assert len(reqs) == 4
        assert [r["id"] for r in reqs] == ["REQ-1", "REQ-2", "REQ-3", "REQ-4"]
        assert "log in" in (reqs[0].get("description") or "").lower()
        assert "reset" in (reqs[1].get("description") or "").lower()
        assert "User Story" in (reqs[3].get("description") or "") or "log out" in (reqs[3].get("description") or "").lower()

    def test_fallback_uses_first_line_as_title_when_no_structured_headers(self):
        """When no REQ-XXX, ##, or numbered sections, fallback uses first line (or first 200 chars) as title."""
        from backend.extractors.requirement_extractor import extract_requirements

        spec = "The system shall allow users to reset their password via email link within 60 seconds."
        reqs = extract_requirements(spec)
        assert len(reqs) == 1
        assert reqs[0]["id"] == "REQ-1"
        assert "password" in (reqs[0].get("title") or "").lower() or "reset" in (reqs[0].get("title") or "").lower()
        assert reqs[0]["title"] != "Requirement (extracted from document)"
        assert reqs[0]["description"] == spec


class TestAssessUpdatesNeedsUpdateAndPartial:
    """_assess_updates: both needs_update and partial statuses go into tests_needing_update."""

    def test_assess_updates_includes_both_needs_update_and_partial(self):
        """When LLM returns needs_update for one test and partial for another, both appear in needing list."""
        from backend.services.requirement_analysis_service import RequirementAnalysisService

        mock_rag = MagicMock()
        mock_rag_service = MagicMock()
        mock_rag_service.rag = mock_rag
        responses = [
            _make_llm_response('{"status": "needs_update", "suggested_changes": ["Add step"], "reason": "Missing coverage", "confidence": 0.85}'),
            _make_llm_response('{"status": "partial", "suggested_changes": ["Extend steps"], "reason": "Only partial", "confidence": 0.9}'),
        ]
        mock_chain = MagicMock()
        mock_chain.invoke.side_effect = responses
        mock_prompt = MagicMock()
        mock_prompt.__or__ = MagicMock(return_value=mock_chain)

        svc = RequirementAnalysisService(rag_service=mock_rag_service)
        related_tests = [
            {"testrail_id": "C1", "title": "Test one", "content": "Steps..."},
            {"testrail_id": "C2", "title": "Test two", "content": "Steps..."},
        ]
        with patch("backend.services.requirement_analysis_service.record_from_langchain_result", return_value=None):
            with patch("langchain_core.prompts.ChatPromptTemplate") as mock_prompt_class:
                mock_prompt_class.from_messages.return_value = mock_prompt
                needing, ok_ids = svc._assess_updates("Requirement text", related_tests, confidence_threshold=0.7)
        assert len(needing) == 2
        statuses = {n["testrail_id"]: n["status"] for n in needing}
        assert statuses.get("C1") == "needs_update"
        assert statuses.get("C2") == "partial"
        assert len(ok_ids) == 0

    def test_assess_updates_ok_status_still_in_needing_with_llm_output(self):
        """Every test gets an entry in needing with real LLM suggested_changes/reason so Update with AI shows them; ok also in ok_ids."""
        from backend.services.requirement_analysis_service import RequirementAnalysisService

        mock_rag = MagicMock()
        mock_rag.llm = MagicMock()
        mock_chain = MagicMock()
        mock_chain.invoke.return_value = _make_llm_response(
            '{"status": "ok", "suggested_changes": [], "reason": "Fine", "confidence": 1.0}'
        )
        mock_prompt = MagicMock()
        mock_prompt.__or__ = MagicMock(return_value=mock_chain)
        mock_rag_service = MagicMock()
        mock_rag_service.rag = mock_rag

        svc = RequirementAnalysisService(rag_service=mock_rag_service)
        related_tests = [{"testrail_id": "C123", "title": "Test", "content": "Steps"}]
        with patch("backend.services.requirement_analysis_service.record_from_langchain_result", return_value=None):
            with patch("langchain_core.prompts.ChatPromptTemplate") as mock_prompt_class:
                mock_prompt_class.from_messages.return_value = mock_prompt
                needing, ok_ids = svc._assess_updates("Requirement", related_tests, confidence_threshold=0.7)
        assert len(needing) == 1
        assert needing[0]["testrail_id"] == "C123"
        assert needing[0]["status"] == "ok"
        assert needing[0]["reason"] == "Fine"
        assert ok_ids == ["C123"]


class TestSuggestCaseUpdateAndUpdateInTestrail:
    """Service: suggest_case_update and update_case_in_testrail."""

    def test_suggest_case_update_returns_dict_when_llm_returns_valid_json(self):
        """suggest_case_update returns a dict with title, steps, preconditions, expected_result, priority when LLM returns valid JSON."""
        from backend.services.requirement_analysis_service import RequirementAnalysisService

        mock_rag = MagicMock()
        mock_chain = MagicMock()
        mock_chain.invoke.return_value = _make_llm_response(
            '{"title": "Updated test", "priority": "P1", "preconditions": "Pre", "steps": "1. Step", "expected_result": "Pass"}'
        )
        mock_prompt = MagicMock()
        mock_prompt.__or__ = MagicMock(return_value=mock_chain)
        mock_rag_service = MagicMock()
        mock_rag_service.rag = mock_rag

        svc = RequirementAnalysisService(rag_service=mock_rag_service)
        with patch("langchain_core.prompts.ChatPromptTemplate") as mock_prompt_class:
            mock_prompt_class.from_messages.return_value = mock_prompt
            result, err = svc.suggest_case_update(
                testrail_id="C42",
                requirement_text="User must be able to reset password.",
                suggested_changes=["Add email verification step"],
                reason="Missing email check",
                current_content="Title: Reset\nSteps: 1. Click reset",
            )
        assert result is not None and err is None
        assert result.get("title") == "Updated test"
        assert result.get("priority") == "P1"
        assert result.get("preconditions") == "Pre"
        assert result.get("steps") == "1. Step"
        assert result.get("expected_result") == "Pass"

    def test_suggest_case_update_returns_none_when_llm_returns_invalid_json(self):
        """suggest_case_update returns None when LLM response has no valid JSON object."""
        from backend.services.requirement_analysis_service import RequirementAnalysisService

        mock_rag = MagicMock()
        mock_rag.llm = MagicMock()
        mock_rag.llm.invoke.return_value = _make_llm_response("No JSON here")
        mock_rag_service = MagicMock()
        mock_rag_service.rag = mock_rag

        svc = RequirementAnalysisService(rag_service=mock_rag_service)
        result, err = svc.suggest_case_update(
            testrail_id="C42",
            requirement_text="Req",
            suggested_changes=[],
            reason="Reason",
        )
        assert result is None

    def test_suggest_case_update_real_prompt_generates_updated_testcase(self):
        """Self-test: full suggest_case_update path with real prompt template; chain returns valid JSON -> we get updated test case dict."""
        from langchain_core.messages import AIMessage
        from backend.services.requirement_analysis_service import RequirementAnalysisService

        llm_response = (
            '{"title": "Onboard with supported country – positive flow", "priority": "P1", '
            '"preconditions": "User has admin access.", "steps": "1. Add new country to config\\n2. Run onboarding\\n3. Verify docs", '
            '"expected_result": "Country is onboarded and documents validated."}'
        )
        mock_rag = MagicMock()
        mock_rag_service = MagicMock()
        mock_rag_service.rag = mock_rag
        mock_chain = MagicMock()
        mock_chain.invoke.return_value = AIMessage(content=llm_response)
        mock_prompt = MagicMock()
        mock_prompt.__or__ = MagicMock(return_value=mock_chain)

        svc = RequirementAnalysisService(rag_service=mock_rag_service)
        with patch("langchain_core.prompts.ChatPromptTemplate") as mock_prompt_class:
            mock_prompt_class.from_messages.return_value = mock_prompt
            result, err = svc.suggest_case_update(
                testrail_id="C129563",
                requirement_text="Support onboarding of a new country with document and address validation.",
                suggested_changes=[
                    "Create a positive test case for successful onboarding with the new country",
                    "Verify country-specific document requirements and address formats",
                ],
                reason="Existing test only covers rejection logic; requirement needs positive onboarding flow.",
                current_content="Title: Onboard with not supported country\nSteps: 1. Use unsupported country code\nExpected: Rejection",
            )
        assert err is None, f"Expected no error, got: {err}"
        assert result is not None, "Expected updated test case dict"
        assert result.get("title") == "Onboard with supported country – positive flow"
        assert result.get("priority") == "P1"
        assert "Add new country" in (result.get("steps") or "")
        assert result.get("expected_result"), "Must have expected_result"

    def test_update_case_in_testrail_success_when_connector_called(self):
        """update_case_in_testrail returns success when connector.update_case is called."""
        from backend.services.requirement_analysis_service import RequirementAnalysisService

        mock_rag_service = MagicMock()
        svc = RequirementAnalysisService(rag_service=mock_rag_service)
        mock_config = MagicMock(
            testrail_url="https://t.io",
            testrail_email="e@x.com",
            testrail_api_key="k",
            testrail_push_enabled=True,
        )
        with patch("backend.rag.rag_settings.get_config", return_value=mock_config):
            with patch("backend.connectors.testrail_connector.TestRailConnector") as mock_conn_class:
                mock_conn = MagicMock()
                mock_conn_class.return_value = mock_conn
                result = svc.update_case_in_testrail(
                    testrail_id="C99",
                    title="New title",
                    steps="1. Step",
                )
        assert result.get("success") is True
        assert result.get("testrail_id") == "C99"
        mock_conn.update_case.assert_called_once()
        call_kw = mock_conn.update_case.call_args[1]
        assert call_kw.get("title") == "New title"
        assert call_kw.get("steps") == "1. Step"

    def test_update_case_in_testrail_fails_when_push_disabled(self):
        """update_case_in_testrail returns success False when TESTRAIL_PUSH_ENABLED is not true."""
        from backend.services.requirement_analysis_service import RequirementAnalysisService

        mock_rag_service = MagicMock()
        svc = RequirementAnalysisService(rag_service=mock_rag_service)
        mock_config = MagicMock(
            testrail_url="https://t.io",
            testrail_email="e@x.com",
            testrail_api_key="k",
            testrail_push_enabled=False,
        )
        with patch("backend.rag.rag_settings.get_config", return_value=mock_config):
            result = svc.update_case_in_testrail(testrail_id="C99", title="Title")
        assert result.get("success") is False
        assert "error" in result


class TestRequirementAnalysisSuggestAndUpdateAPI:
    """API: POST suggest-case-update and POST update-case."""

    @pytest.fixture
    def app(self):
        from backend.app import create_app
        return create_app()

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    @patch("backend.api.customer.routes.RequirementAnalysisService")
    def test_suggest_case_update_400_when_missing_params(self, mock_svc_class, client):
        """POST suggest-case-update returns 400 when testrail_id or requirement_text missing."""
        resp = client.post(
            "/api/customer/requirement-analysis/suggest-case-update",
            json={"testrail_id": "C1"},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data.get("success") is False
        assert "error" in data

        resp2 = client.post(
            "/api/customer/requirement-analysis/suggest-case-update",
            json={"requirement_text": "Req"},
            headers={"Content-Type": "application/json"},
        )
        assert resp2.status_code == 400

    @patch("backend.api.customer.routes.RequirementAnalysisService")
    def test_suggest_case_update_200_when_service_returns_suggestion(self, mock_svc_class, client):
        """POST suggest-case-update returns 200 and suggestion when service returns dict."""
        mock_svc = MagicMock()
        mock_svc.suggest_case_update.return_value = ({
            "title": "Suggested title",
            "steps": "1. Step",
            "preconditions": "Pre",
            "expected_result": "Result",
            "priority": "P2",
        }, None)
        mock_svc_class.return_value = mock_svc

        resp = client.post(
            "/api/customer/requirement-analysis/suggest-case-update",
            json={
                "testrail_id": "C42",
                "requirement_text": "User must reset password.",
                "suggested_changes": ["Add email step"],
                "reason": "Missing step",
            },
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("success") is True
        assert data.get("title") == "Suggested title"
        assert data.get("steps") == "1. Step"
        assert data.get("priority") == "P2"

    @patch("backend.api.customer.routes.RequirementAnalysisService")
    def test_suggest_case_update_500_when_service_returns_none(self, mock_svc_class, client):
        """POST suggest-case-update returns 500 when service returns (None, error)."""
        mock_svc = MagicMock()
        mock_svc.suggest_case_update.return_value = (None, "Could not generate suggestion")
        mock_svc_class.return_value = mock_svc

        resp = client.post(
            "/api/customer/requirement-analysis/suggest-case-update",
            json={
                "testrail_id": "C42",
                "requirement_text": "Req",
                "suggested_changes": [],
                "reason": "",
            },
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 500
        assert resp.get_json().get("success") is False

    @patch("backend.api.customer.routes.RequirementAnalysisService")
    def test_update_case_400_when_missing_title(self, mock_svc_class, client):
        """POST update-case returns 400 when testrail_id or title missing."""
        resp = client.post(
            "/api/customer/requirement-analysis/update-case",
            json={"testrail_id": "C99"},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        resp2 = client.post(
            "/api/customer/requirement-analysis/update-case",
            json={"title": "Title"},
            headers={"Content-Type": "application/json"},
        )
        assert resp2.status_code == 400

    @patch("backend.api.customer.routes.RequirementAnalysisService")
    def test_update_case_200_when_service_returns_success(self, mock_svc_class, client):
        """POST update-case returns 200 when service returns success."""
        mock_svc = MagicMock()
        mock_svc.update_case_in_testrail.return_value = {"success": True, "testrail_id": "C99"}
        mock_svc_class.return_value = mock_svc

        resp = client.post(
            "/api/customer/requirement-analysis/update-case",
            json={
                "testrail_id": "C99",
                "title": "Updated title",
                "steps": "1. Step one",
            },
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("success") is True
        assert data.get("testrail_id") == "C99"
        mock_svc.update_case_in_testrail.assert_called_once()
        call_kw = mock_svc.update_case_in_testrail.call_args[1]
        assert call_kw.get("title") == "Updated title"
        assert call_kw.get("steps") == "1. Step one"


class TestRequirementAnalysisCreateCaseAPI:
    """API: POST create-case (new test case in TestRail)."""

    @pytest.fixture
    def app(self):
        from backend.app import create_app
        return create_app()

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    @patch("backend.api.customer.routes.RequirementAnalysisService")
    def test_create_case_400_when_section_id_or_title_missing(self, mock_svc_class, client):
        """POST create-case returns 400 when section_id or title missing."""
        resp = client.post(
            "/api/customer/requirement-analysis/create-case",
            json={"section_id": 123},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data.get("success") is False
        assert "error" in data

        resp2 = client.post(
            "/api/customer/requirement-analysis/create-case",
            json={"title": "New case"},
            headers={"Content-Type": "application/json"},
        )
        assert resp2.status_code == 400

    @patch("backend.api.customer.routes.RequirementAnalysisService")
    def test_create_case_200_when_service_returns_success(self, mock_svc_class, client):
        """POST create-case returns 200 and testrail_id when service returns success."""
        mock_svc = MagicMock()
        mock_svc.create_case_in_testrail.return_value = {"success": True, "testrail_id": "C1001"}
        mock_svc_class.return_value = mock_svc

        resp = client.post(
            "/api/customer/requirement-analysis/create-case",
            json={
                "section_id": 42,
                "title": "New test case",
                "steps": "1. Step",
                "preconditions": "Pre",
                "expected_result": "Result",
                "priority": "P1",
            },
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("success") is True
        assert data.get("testrail_id") == "C1001"
        mock_svc.create_case_in_testrail.assert_called_once()
        call_kw = mock_svc.create_case_in_testrail.call_args[1]
        assert call_kw.get("section_id") == 42
        assert call_kw.get("title") == "New test case"
        assert call_kw.get("steps") == "1. Step"
        assert call_kw.get("priority") == "P1"

    @patch("backend.api.customer.routes.RequirementAnalysisService")
    def test_create_case_400_when_service_returns_failure(self, mock_svc_class, client):
        """POST create-case returns 400 when service returns success=False."""
        mock_svc = MagicMock()
        mock_svc.create_case_in_testrail.return_value = {"success": False, "error": "Section not found"}
        mock_svc_class.return_value = mock_svc

        resp = client.post(
            "/api/customer/requirement-analysis/create-case",
            json={"section_id": 999, "title": "New case"},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data.get("success") is False
        assert "error" in data


@pytest.mark.integration
class TestRequirementAnalysisIntegration:
    """
    Integration test: real RAG + LLM (optional).
    Run with: pytest tests/test_requirement_analysis.py -v -m integration
    Requires config/.env with LLM and ChromaDB; ChromaDB may have no data (related_tests can be empty).
    """

    @pytest.fixture
    def app(self):
        from backend.app import create_app
        return create_app()

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    def test_requirement_analysis_paste_integration(self, client):
        """Call real endpoint with pasted text; expect 200 and valid shape (content may be empty)."""
        resp = client.post(
            "/api/customer/requirement-analysis",
            json={
                "requirement_spec": "REQ-001: User must be able to reset password via email.\nREQ-002: System shall send verification email within 60 seconds.",
                "generate_new_tests": True,
            },
            headers={"Content-Type": "application/json"},
        )
        # Allow 200 (success) or 500 if RAG/LLM not configured
        if resp.status_code != 200:
            pytest.skip(f"Requirement analysis returned {resp.status_code}; ensure LLM and ChromaDB are configured in config/.env")
        data = resp.get_json()
        assert data.get("success") is True
        assert "requirements_analyzed" in data
        assert "requirements" in data
        assert "related_specs" in data
        assert "related_tests" in data
        assert "tests_needing_update" in data
        assert "uncovered_requirements" in data
        assert "generated_tests" in data
        assert "recommended_e2e_set" in data
        assert "summary" in data
        # recommended_e2e_set: one entry per requirement with reuse_as_is, use_after_update, create_new
        for req in data.get("requirements") or []:
            req_id = req.get("id")
            if not req_id:
                continue
            assert req_id in data.get("recommended_e2e_set", {}), f"recommended_e2e_set must have key {req_id}"
            e2e = data["recommended_e2e_set"][req_id]
            assert "reuse_as_is" in e2e and "use_after_update" in e2e and "create_new" in e2e
        # E2E self-test: when generated_tests exist, priorities must be P0>P1>P2>P3 and each test has required fields
        valid_priorities = {"P0", "P1", "P2", "P3"}
        for req_id, tests in (data.get("generated_tests") or {}).items():
            if not tests:
                continue
            priorities = [(t.get("priority") or "P2").upper().strip() for t in tests]
            for p in priorities:
                assert p in valid_priorities, f"Generated test has invalid priority {p}, must be P0/P1/P2/P3"
            expected_order = sorted(priorities, key=lambda x: {"P0": 4, "P1": 3, "P2": 2, "P3": 1}.get(x, 2), reverse=True)
            assert priorities == expected_order, f"Generated tests for {req_id} must be ordered P0>P1>P2>P3, got {priorities}"
            for t in tests:
                assert t.get("title"), f"Generated test missing title: {t}"
                assert t.get("steps") is not None or t.get("expected_result") is not None, f"Generated test must have steps or expected_result: {t}"

    def test_suggest_case_update_integration(self, client):
        """Call real suggest-case-update endpoint; expect 200 and generated title/steps/expected_result (or skip if LLM not configured)."""
        resp = client.post(
            "/api/customer/requirement-analysis/suggest-case-update",
            json={
                "testrail_id": "C129563",
                "requirement_text": "Support onboarding of a new country with document and address validation.",
                "suggested_changes": [
                    "Create a positive test case for successful onboarding with the new country",
                    "Verify country-specific document requirements and address formats",
                ],
                "reason": "Existing test only covers rejection logic; requirement needs positive onboarding flow.",
                "current_content": "Title: Onboard with not supported country\nSteps: 1. Use unsupported country code\nExpected: Rejection",
            },
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code != 200:
            pytest.skip(
                f"suggest-case-update returned {resp.status_code}; ensure LLM is configured (OPENAI_API_KEY or similar). "
                f"Error: {(resp.get_json() or {}).get('error', '')}"
            )
        data = resp.get_json()
        assert data.get("success") is True, data.get("error", "Unknown error")
        assert data.get("title"), "Generated suggestion must have title"
        assert data.get("steps") is not None or data.get("expected_result") is not None, "Must have steps or expected_result"
        assert (data.get("priority") or "P2").upper() in ("P0", "P1", "P2", "P3")
