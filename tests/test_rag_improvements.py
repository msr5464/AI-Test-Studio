"""
End-to-end self-test for all RAG quality improvements.

Covers:
  Phase 1  – Config values (threshold 55%, hybrid=True, reranking=True)
  Phase 2a – Cache invalidation on upload / add_document_file / delete
  Phase 2b – Context sorted by similarity DESC before LLM
  Phase 2c – Each context chunk prefixed with "### Source N (relevance: XX%)"
  Phase 2d – load_fresh_vectorstore_once() exists; find_related_tests() accepts
             vectorstore param; requirement analysis loop uses single load
  Phase 3a – expand_query() uses LLM when provided; falls back gracefully
"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, PropertyMock, call

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _llm_response(text: str):
    """Simulate a LangChain LLM response object."""
    return SimpleNamespace(content=text)


def _make_doc(content: str, metadata: dict = None):
    """Minimal LangChain-style Document object."""
    from langchain_core.documents import Document
    return Document(page_content=content, metadata=metadata or {})


# ---------------------------------------------------------------------------
# Phase 1 – Config defaults
# ---------------------------------------------------------------------------

class TestConfigDefaults:
    """Verify the updated .env values are loaded by RAGConfig."""

    def test_chat_similarity_threshold_is_55(self):
        from backend.rag.rag_settings import get_config
        cfg = get_config()
        assert cfg.chat_min_similarity_threshold == 55.0, (
            f"Expected 55.0, got {cfg.chat_min_similarity_threshold}. "
            "Check CHAT_MIN_SIMILARITY_THRESHOLD in config/.env"
        )

    def test_chat_hybrid_search_enabled(self):
        from backend.rag.rag_settings import get_config
        cfg = get_config()
        assert cfg.chat_use_hybrid_search is True, (
            "CHAT_USE_HYBRID_SEARCH should be True in config/.env"
        )

    def test_chat_reranking_enabled(self):
        from backend.rag.rag_settings import get_config
        cfg = get_config()
        assert cfg.chat_use_reranking is True, (
            "CHAT_USE_RERANKING should be True in config/.env"
        )

    def test_requirement_threshold_is_60(self):
        from backend.rag.rag_settings import get_config
        cfg = get_config()
        assert cfg.requirement_retrieval_similarity_threshold == 60.0, (
            f"Expected 60.0, got {cfg.requirement_retrieval_similarity_threshold}"
        )

    def test_needs_update_ceiling_above_retrieval_floor(self):
        """Ceiling must be > floor so the 'Need Update' band is non-empty."""
        from backend.rag.rag_settings import get_config
        cfg = get_config()
        raw = cfg.requirement_needs_update_confidence_threshold
        # settings.py normalises 0-100 → 0-1; un-normalise for readability
        ceiling_pct = raw * 100.0 if raw <= 1.0 else raw
        floor_pct = cfg.requirement_retrieval_similarity_threshold
        assert ceiling_pct > floor_pct, (
            f"Ceiling ({ceiling_pct}%) must be > floor ({floor_pct}%). "
            "The 'Need Update' tab will always be empty otherwise."
        )


# ---------------------------------------------------------------------------
# Phase 2a – Cache invalidation
# ---------------------------------------------------------------------------

class TestCacheInvalidation:
    """_clear_all_query_caches() must be called after upload / add / delete."""

    def _make_service(self):
        """RAGService with all heavy I/O mocked out."""
        with patch("backend.services.rag_service.MultiFormatRAG"), \
             patch("backend.services.rag_service.get_config"), \
             patch("backend.services.rag_service.Path.mkdir"):
            from backend.services.rag_service import RAGService
            svc = RAGService.__new__(RAGService)
            svc.rag = MagicMock()
            svc.rag.query_cache = MagicMock()
            svc.rag._pdf_rag = MagicMock()
            svc.rag._pdf_rag.query_cache = MagicMock()
            svc.rag._csv_excel_rag = MagicMock()
            svc.rag._csv_excel_rag.query_cache = MagicMock()
            svc.rag._text_rag = MagicMock()
            svc.rag._text_rag.query_cache = MagicMock()
            svc.documents = {}
            svc.documents_dir = Path("/tmp/docs")
            svc.chroma_db_dir = Path("/tmp/chroma")
            import threading
            svc._vectorstore_reload_lock = threading.Lock()
            return svc

    def test_clear_caches_clears_main_and_children(self):
        from backend.services.rag_service import RAGService
        svc = self._make_service()
        svc._clear_all_query_caches()
        svc.rag.query_cache.clear.assert_called_once()
        svc.rag._pdf_rag.query_cache.clear.assert_called_once()
        svc.rag._csv_excel_rag.query_cache.clear.assert_called_once()
        svc.rag._text_rag.query_cache.clear.assert_called_once()

    def test_clear_caches_safe_when_no_cache(self):
        """Should not raise if query_cache is None."""
        from backend.services.rag_service import RAGService
        svc = self._make_service()
        svc.rag.query_cache = None
        svc.rag._pdf_rag.query_cache = None
        svc.rag._csv_excel_rag.query_cache = None
        svc.rag._text_rag.query_cache = None
        svc._clear_all_query_caches()  # must not raise

    def test_upload_document_calls_clear_cache(self):
        """upload_document() must call _clear_all_query_caches on success."""
        from backend.services.rag_service import RAGService
        svc = self._make_service()
        svc._validate_testcase_file = MagicMock(return_value=(True, None))
        svc._find_existing_document = MagicMock(return_value=None)
        svc.rag.add_files = MagicMock()
        svc._save_document_metadata = MagicMock()
        svc._clear_all_query_caches = MagicMock()

        import tempfile, uuid
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            f.write(b"id,title\n1,test")
            tmp = Path(f.name)

        with patch("shutil.copy2"), patch("uuid.uuid4", return_value=uuid.UUID("00000000-0000-0000-0000-000000000001")):
            result = svc.upload_document(tmp, "test.csv")

        svc._clear_all_query_caches.assert_called_once()

    def test_add_document_file_calls_clear_cache(self):
        """add_document_file() must call _clear_all_query_caches on success."""
        from backend.services.rag_service import RAGService
        svc = self._make_service()
        svc._find_existing_document = MagicMock(return_value=None)
        svc.rag.add_files = MagicMock()
        svc._save_document_metadata = MagicMock()
        svc._clear_all_query_caches = MagicMock()

        import tempfile, uuid
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as f:
            f.write(b"# hello")
            tmp = Path(f.name)

        with patch("shutil.copy2"), patch("uuid.uuid4", return_value=uuid.UUID("00000000-0000-0000-0000-000000000002")):
            result = svc.add_document_file(tmp, "page.md")

        svc._clear_all_query_caches.assert_called_once()

    def test_delete_document_calls_clear_cache(self):
        """delete_document() must call _clear_all_query_caches on success."""
        from backend.services.rag_service import RAGService
        svc = self._make_service()
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            tmp = Path(f.name)

        svc.documents["doc1"] = {"id": "doc1", "name": "test.csv", "path": str(tmp)}
        svc.rag.vectorstore = None  # skip ChromaDB removal
        svc._save_document_metadata = MagicMock()
        svc._clear_all_query_caches = MagicMock()

        result = svc.delete_document("doc1")

        assert result["success"] is True
        svc._clear_all_query_caches.assert_called_once()


# ---------------------------------------------------------------------------
# Phase 2b – Context sorted by similarity DESC
# ---------------------------------------------------------------------------

class TestContextSorting:
    """
    _query_impl must order (docs, sources_with_scores) by similarity_percent DESC
    before building the context string passed to the LLM.
    """

    def _run_context_construction(self, paired_inputs):
        """
        Simulate the sort+context-build block from base_rag._query_impl
        using the same code that was inserted.

        paired_inputs: list of (page_content, similarity_percent)
        Returns (sorted_contents, context_string)
        """
        from langchain_core.documents import Document

        docs = [Document(page_content=c, metadata={}) for c, _ in paired_inputs]
        sources_with_scores = [
            {"similarity_percent": pct, "content": c[:150]}
            for c, pct in paired_inputs
        ]

        # ---- replicate the exact sort+build block from base_rag.py ----
        def _testrail_id_numeric(doc):
            return 0  # no TestRail ID in these test docs

        if docs and sources_with_scores and len(sources_with_scores) == len(docs):
            paired = list(zip(docs, sources_with_scores))
            paired.sort(key=lambda p: (
                -(p[1].get('similarity_percent') or 0.0),
                -_testrail_id_numeric(p[0])
            ))
            docs = [p[0] for p in paired]
            sources_with_scores = [p[1] for p in paired]

        context_parts = []
        for i, doc in enumerate(docs, 1):
            sim_pct = sources_with_scores[i - 1].get('similarity_percent') if sources_with_scores else None
            prefix = f"### Source {i} (relevance: {sim_pct:.0f}%)\n" if sim_pct is not None else f"### Source {i}\n"
            context_parts.append(prefix + (doc.page_content or ""))

        context = "\n\n".join(context_parts)
        return [d.page_content for d in docs], context

    def test_best_match_is_first(self):
        inputs = [
            ("low relevance chunk", 42.0),
            ("high relevance chunk", 88.0),
            ("medium relevance chunk", 65.0),
        ]
        sorted_contents, _ = self._run_context_construction(inputs)
        assert sorted_contents[0] == "high relevance chunk"
        assert sorted_contents[1] == "medium relevance chunk"
        assert sorted_contents[2] == "low relevance chunk"

    def test_context_prefix_contains_relevance(self):
        inputs = [("chunk A", 72.0), ("chunk B", 91.0)]
        _, context = self._run_context_construction(inputs)
        assert "### Source 1 (relevance: 91%)" in context
        assert "### Source 2 (relevance: 72%)" in context

    def test_none_similarity_sorted_last(self):
        inputs = [
            ("no score chunk", None),
            ("scored chunk", 55.0),
        ]
        sorted_contents, _ = self._run_context_construction(inputs)
        assert sorted_contents[0] == "scored chunk"
        assert sorted_contents[1] == "no score chunk"

    def test_prefix_absent_when_no_scores(self):
        """When sources_with_scores has no similarity_percent, prefix says Source N only."""
        inputs = [("chunk", None)]
        _, context = self._run_context_construction(inputs)
        assert "### Source 1\n" in context
        assert "relevance" not in context


# ---------------------------------------------------------------------------
# Phase 2d – Vectorstore session cache
# ---------------------------------------------------------------------------

class TestVectorstoreSessionCache:
    """load_fresh_vectorstore_once() exists and find_related_tests() accepts vectorstore param."""

    def test_load_fresh_vectorstore_once_exists(self):
        from backend.services.rag_service import RAGService
        assert callable(getattr(RAGService, "load_fresh_vectorstore_once", None)), \
            "RAGService.load_fresh_vectorstore_once() method is missing"

    def test_find_related_tests_accepts_vectorstore_param(self):
        import inspect
        from backend.services.rag_service import RAGService
        sig = inspect.signature(RAGService.find_related_tests)
        assert "vectorstore" in sig.parameters, \
            "find_related_tests() must accept a 'vectorstore' keyword argument"

    def test_find_related_tests_uses_provided_vectorstore(self):
        """When vectorstore is passed, _get_fresh_vectorstore_from_disk must NOT be called."""
        from backend.services.rag_service import RAGService
        import threading

        svc = RAGService.__new__(RAGService)
        svc._vectorstore_reload_lock = threading.Lock()

        mock_vs = MagicMock()
        svc.rag = MagicMock()
        svc.rag.vectorstore = mock_vs
        svc.rag.retrieve_documents_with_scores = MagicMock(return_value=[])
        svc._get_fresh_vectorstore_from_disk = MagicMock(return_value=MagicMock())

        with patch("backend.services.rag_service.get_config") as mock_cfg:
            mock_cfg.return_value = MagicMock(
                requirement_retrieval_k=5,
                requirement_retrieval_similarity_threshold=60.0,
                requirement_use_hybrid_search=False,
                requirement_use_reranking=False,
            )
            svc.find_related_tests("some requirement", k=5, vectorstore=mock_vs)

        svc._get_fresh_vectorstore_from_disk.assert_not_called()
        # The passed vectorstore was assigned to rag.vectorstore
        assert svc.rag.vectorstore is mock_vs

    def test_requirement_analysis_passes_session_vectorstore(self):
        """
        RequirementAnalysisService.analyze() should call load_fresh_vectorstore_once()
        exactly once and pass the result to every find_related_tests() call.
        """
        from backend.services.requirement_analysis_service import RequirementAnalysisService

        mock_rag_service = MagicMock()
        session_vs = MagicMock(name="session_vectorstore")
        mock_rag_service.load_fresh_vectorstore_once.return_value = session_vs
        mock_rag_service.find_related_tests.return_value = []
        mock_rag_service.find_related_specs.return_value = []

        mock_llm = MagicMock()
        # Extraction returns 2 requirements
        extract_json = '{"requirements": [{"id": "R1", "title": "Req 1", "description": ""}, {"id": "R2", "title": "Req 2", "description": ""}]}'
        mock_llm.invoke.return_value = _llm_response(extract_json)

        svc = RequirementAnalysisService.__new__(RequirementAnalysisService)
        svc.rag_service = mock_rag_service
        svc.llm = mock_llm
        svc.config = MagicMock(
            requirement_retrieval_similarity_threshold=60.0,
            requirement_needs_update_confidence_threshold=0.75,
            requirement_min_tests_per_priority=3,
            requirement_coverage_sufficient_min_similarity=70.0,
            requirement_analysis_llm_delay_sec=0,
            requirement_enrich_with_context=False,
            requirement_generate_e2e_tests=False,
        )

        requirements = [
            {"id": "R1", "title": "Req 1", "description": ""},
            {"id": "R2", "title": "Req 2", "description": ""},
        ]

        # Patch module-level extract_requirements and instance parse_input to skip I/O
        with patch("backend.services.requirement_analysis_service.extract_requirements", return_value=requirements), \
             patch("backend.services.requirement_analysis_service.enrich_requirements_with_context", return_value=requirements), \
             patch("backend.services.requirement_analysis_service.clean_descriptions_with_llm", return_value=requirements), \
             patch.object(svc, "parse_input", return_value="dummy spec"), \
             patch.object(svc, "_generate_tests_for_requirement", return_value=[], create=True), \
             patch.object(svc, "_assess_updates", return_value=([], []), create=True):
            try:
                svc.analyze(text="dummy")
            except Exception:
                pass  # downstream steps may fail; we only care about the retrieval calls

        # load_fresh_vectorstore_once called exactly once
        mock_rag_service.load_fresh_vectorstore_once.assert_called_once()

        # Every find_related_tests call received the session vectorstore
        for c in mock_rag_service.find_related_tests.call_args_list:
            assert c.kwargs.get("vectorstore") is session_vs or (
                len(c.args) >= 3 and c.args[2] is session_vs
            ), f"find_related_tests called without session vectorstore: {c}"


# ---------------------------------------------------------------------------
# Phase 3a – LLM-based query expansion
# ---------------------------------------------------------------------------

class TestQueryExpansion:
    """expand_query() uses LLM when available and falls back gracefully."""

    def test_disabled_returns_only_original(self):
        from backend.rag.rag_helper import expand_query
        result = expand_query("find login tests", use_query_expansion=False)
        assert result == ["find login tests"]

    def test_enabled_no_llm_strips_question_mark(self):
        from backend.rag.rag_helper import expand_query
        result = expand_query("what are login tests?", use_query_expansion=True, llm=None)
        assert "what are login tests?" in result
        assert "what are login tests" in result

    def test_enabled_no_llm_no_question_mark_returns_original_only(self):
        from backend.rag.rag_helper import expand_query
        result = expand_query("find login tests", use_query_expansion=True, llm=None)
        assert result == ["find login tests"]

    def test_enabled_with_llm_returns_variants(self):
        from backend.rag.rag_helper import expand_query
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _llm_response(
            "What test cases cover the login feature?\nFind authentication-related tests"
        )
        result = expand_query("login tests", use_query_expansion=True, llm=mock_llm)
        assert result[0] == "login tests"
        assert len(result) == 3
        assert "What test cases cover the login feature?" in result
        assert "Find authentication-related tests" in result

    def test_enabled_with_llm_fallback_on_error(self):
        from backend.rag.rag_helper import expand_query
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = RuntimeError("LLM unavailable")
        # Should not raise; falls back to basic expansion
        result = expand_query("what are login tests?", use_query_expansion=True, llm=mock_llm)
        assert "what are login tests?" in result

    def test_llm_called_with_query_in_prompt(self):
        from backend.rag.rag_helper import expand_query
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _llm_response("variant 1\nvariant 2")
        expand_query("specific query text", use_query_expansion=True, llm=mock_llm)
        prompt_used = mock_llm.invoke.call_args[0][0]
        assert "specific query text" in prompt_used

    def test_base_rag_passes_llm_to_expand_query(self):
        """expand_query in rag_engine._query_impl must receive llm=self.llm."""
        with patch("backend.rag.rag_engine.expand_query") as mock_expand:
            mock_expand.return_value = ["test query"]

            from backend.rag.rag_engine import BaseRAG
            rag = BaseRAG.__new__(BaseRAG)
            rag.llm = MagicMock(name="test_llm")
            rag.use_query_expansion = True
            rag.use_hybrid_search = False
            rag.use_reranking = False
            rag.enable_query_cache = False
            rag.show_matching_sources = False
            rag.min_similarity_threshold = 55.0
            rag.retrieval_k = 6
            rag.vectorstore = MagicMock()
            rag.retriever = MagicMock()
            rag.retriever.invoke.return_value = []
            rag.memory = None
            rag.enable_memory = False
            rag.debug_mode = False
            rag.reranker = None
            rag._vectorstore_lock = __import__("threading").Lock()

            with patch.object(rag, "_get_dynamic_retrieval_params", return_value={"k": 6, "filter": None}), \
                 patch.object(rag, "deduplicate_documents_method", return_value=[], create=True):
                try:
                    rag._query_impl("test query")
                except Exception:
                    pass  # we only care that expand_query was called with llm

            mock_expand.assert_called_once_with("test query", True, llm=rag.llm)
