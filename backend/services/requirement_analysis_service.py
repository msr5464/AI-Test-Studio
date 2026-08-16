"""
Requirement Analysis Service
============================
Orchestrates requirement spec analysis: find related tests, suggest updates, generate new tests.
Supports three input methods: upload file, Confluence URL, paste text.
"""

import copy
import hashlib
import json
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

# Configurable company name for LLM prompts (set COMPANY_NAME in .env or Admin Settings)
def _get_company_name() -> str:
    return os.getenv("COMPANY_NAME", "your company")

# Module-level cache: spec_text_hash → enriched requirements list.
# Ensures the same document always produces identical requirement titles/descriptions
# across multiple analysis runs within the same server session, making similarity
# scores and related-test counts fully reproducible.
# LRU eviction: oldest entries are evicted when cache exceeds _REQ_CACHE_MAX_SIZE.
_REQ_CACHE_MAX_SIZE = 50
_requirements_cache: Dict[str, List[Dict]] = {}

def _cache_put(key: str, value: List[Dict]) -> None:
    """Insert into LRU cache with eviction."""
    if key in _requirements_cache:
        del _requirements_cache[key]  # re-insert at end (newest)
    elif len(_requirements_cache) >= _REQ_CACHE_MAX_SIZE:
        _oldest = next(iter(_requirements_cache))
        del _requirements_cache[_oldest]
    _requirements_cache[key] = value

# Global semaphore: caps the total number of requirements being actively analysed
# (LLM calls in-flight) across ALL concurrent users.  Without this, 3 users with
# 4+3+2=9 parallel requirements would flood the LLM API, causing every call to
# slow from ~20 s to 60-120 s and pushing total time past 15 min.
# Value of 8 allows multiple users to run in parallel without excessive head-of-line blocking.
# The LLM API's own rate limiting (e.g. Gemini max_retries) provides backpressure.
# Configurable via REQUIREMENT_PARALLEL_WORKERS env var (read at startup).
import os as _os
_LLM_SEM_VALUE: int = int(_os.getenv("REQUIREMENT_PARALLEL_WORKERS", "8"))
_llm_concurrency_sem = __import__("threading").Semaphore(_LLM_SEM_VALUE)

import sys
_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

from backend.extractors.requirement_extractor import extract_requirements, enrich_requirements_with_context, clean_descriptions_with_llm
from backend.cost_tracker import record_from_langchain_result


def _llm_delay_sec() -> float:
    """Optional delay between LLM calls to avoid 429 rate limits (e.g. Gemini). Set REQUIREMENT_ANALYSIS_LLM_DELAY_SEC in .env."""
    try:
        v = os.getenv("REQUIREMENT_ANALYSIS_LLM_DELAY_SEC", "").strip()
        if v:
            return max(0.0, min(10.0, float(v)))
    except ValueError:
        pass
    return 0.0


def _coverage_sufficient_shortcut(related_tests: List[Dict]) -> bool:
    """
    If we already have enough related tests with strong similarity, consider coverage sufficient
    without asking the LLM. This stops the cycle of always generating 5 new tests when the
    requirement is already well covered (e.g. user just pushed 5 tests and re-runs).
    Config: REQUIREMENT_COVERAGE_SUFFICIENT_MIN_TESTS (default 5), REQUIREMENT_TESTS_COVERAGE_MIN_SIMILARITY (0-100, default 70).
    """
    if not related_tests or len(related_tests) < 3:
        return False
    min_tests = 5
    min_sim_pct = 70.0
    try:
        v = os.getenv("REQUIREMENT_COVERAGE_SUFFICIENT_MIN_TESTS", "").strip()
        if v:
            min_tests = max(2, min(20, int(v)))
        v = os.getenv("REQUIREMENT_TESTS_COVERAGE_MIN_SIMILARITY", "").strip()
        if v:
            min_sim_pct = max(50.0, min(100.0, float(v)))
    except (ValueError, TypeError):
        pass
    if len(related_tests) < min_tests:
        return False
    # similarity_score from find_related_tests can be 0-1 or 0-100 depending on RAG
    min_sim = min_sim_pct / 100.0
    for t in related_tests:
        s = t.get("similarity_score")
        if s is None:
            return False  # need scores to trust shortcut
        if s > 1:
            s = s / 100.0  # normalize to 0-1
        if s < min_sim:
            return False
    return True


def _compute_generate_priorities(
    related_tests: List[Dict],
    generate_p2_p3: bool,
    min_per_priority: int = 3,
    ok_ids: Optional[List[str]] = None,
    acceptance_criteria: Optional[List[str]] = None,
) -> List[str]:
    """
    Decide which priorities we still need to generate based on counts of related tests per priority.
    Gate 1 of the two-gate model.
    A test is "eligible" (counts toward coverage) if:
      - similarity_score >= REQUIREMENT_TESTS_COVERAGE_MIN_SIMILARITY (default 70%), OR
      - its testrail_id is in ok_ids (LLM-validated adequate tests from the 75-80% band)
    If we have at least min_per_priority (default 3) eligible tests for each priority, don't generate for that priority.
    When acceptance_criteria are provided and len(ACs) > min_per_priority, the AC count is used as
    the effective threshold — ensuring we have enough tests to plausibly cover every criterion.
    Returns list of priorities still needing generation (e.g. ["P0", "P1"] or ["P1"] or []).
    Config: REQUIREMENT_MIN_TESTS_PER_PRIORITY (default 3), REQUIREMENT_TESTS_COVERAGE_MIN_SIMILARITY (0-100, default 70).
    """
    try:
        v = os.getenv("REQUIREMENT_MIN_TESTS_PER_PRIORITY", "").strip()
        if v:
            min_per_priority = max(1, min(10, int(v)))
    except (ValueError, TypeError):
        pass
    min_sim_pct = 70.0
    try:
        v = os.getenv("REQUIREMENT_TESTS_COVERAGE_MIN_SIMILARITY", "").strip()
        if v:
            min_sim_pct = max(0.0, min(100.0, float(v)))
    except (ValueError, TypeError):
        pass
    min_sim_01 = min_sim_pct / 100.0

    # Only count tests that meet the similarity threshold (scores may be 0-1 or 0-100)
    def _passes_similarity(t: Dict) -> bool:
        s = t.get("similarity_score")
        if s is None:
            return False
        if s > 1:
            s = s / 100.0
        return s >= min_sim_01

    # LLM-validated ok tests from the 75-80% band also count as eligible
    ok_id_set = set(ok_ids) if ok_ids else set()
    eligible = [
        t for t in related_tests
        if _passes_similarity(t)
        or (t.get("testrail_id") or t.get("title") or "N/A") in ok_id_set
    ]
    # Total eligible count: if enough tests overall, coverage is sufficient.
    # Don't require every individual priority to be filled — real test suites
    # often have uneven priority distribution.
    tracked = ["P0", "P1"] + (["P2", "P3"] if generate_p2_p3 else [])
    _total_needed = min_per_priority * len(tracked)
    if len(eligible) >= _total_needed:
        return []  # Enough total coverage

    # Per-priority fallback: if total is insufficient, check which priorities need more
    counts = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
    for t in eligible:
        p = (t.get("priority") or "").strip().upper()
        if p in counts:
            counts[p] += 1
    out = []
    if counts["P0"] < min_per_priority:
        out.append("P0")
    if counts["P1"] < min_per_priority:
        out.append("P1")
    if generate_p2_p3 and counts["P2"] < min_per_priority:
        out.append("P2")
    if generate_p2_p3 and counts["P3"] < min_per_priority:
        out.append("P3")
    return out


def _compute_coverage_metrics(
    related_tests: List[Dict],
    generated_tests_for_req: List[Dict],
    generate_p2_p3: bool,
    ok_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Compute per-requirement coverage % using the same thresholds as _compute_generate_priorities,
    combining existing strong tests (similarity >= min_sim) and newly generated tests.
    Mirrors Gate 1 exactly: ok_ids (LLM-validated tests) count as strong so coverage % matches gate decision.
    """
    min_per_priority = 3
    min_sim_pct = 70.0
    try:
        v = os.getenv("REQUIREMENT_MIN_TESTS_PER_PRIORITY", "3")
        min_per_priority = max(1, min(10, int(v)))
    except (ValueError, TypeError):
        pass
    try:
        v = os.getenv("REQUIREMENT_TESTS_COVERAGE_MIN_SIMILARITY", "70")
        min_sim_pct = max(50.0, min(100.0, float(v)))
    except (ValueError, TypeError):
        pass

    min_sim_01 = min_sim_pct / 100.0

    def _score_01(t: Dict) -> float:
        s = t.get("similarity_score")
        if s is None:
            return 0.0
        return s / 100.0 if s > 1.0 else float(s)

    tracked = ["P0", "P1"] + (["P2", "P3"] if generate_p2_p3 else [])

    # Strong counts: tests at >= coverage similarity threshold OR LLM-validated ok (mirrors Gate 1)
    ok_id_set = set(ok_ids) if ok_ids else set()
    existing_counts: Dict[str, int] = {p: 0 for p in tracked}
    for t in related_tests:
        tid = t.get("testrail_id") or t.get("title") or "N/A"
        if _score_01(t) >= min_sim_01 or tid in ok_id_set:
            p = (t.get("priority") or "").strip().upper()
            if p in existing_counts:
                existing_counts[p] += 1

    # Soft counts: all related tests regardless of threshold (fallback for display when generation is off)
    soft_counts: Dict[str, int] = {p: 0 for p in tracked}
    for t in related_tests:
        p = (t.get("priority") or "").strip().upper()
        if p in soft_counts:
            soft_counts[p] += 1

    generated_counts: Dict[str, int] = {p: 0 for p in tracked}
    for t in generated_tests_for_req:
        p = (t.get("priority") or "").strip().upper()
        if p in generated_counts:
            generated_counts[p] += 1

    total_existing_strong = sum(
        1 for t in related_tests
        if _score_01(t) >= min_sim_01
        or (t.get("testrail_id") or t.get("title") or "N/A") in ok_id_set
    )
    total_generated = len(generated_tests_for_req)

    # Total shortcut: mirrors Gate 1 — if enough tests overall, all priorities are 100%.
    _total_needed = min_per_priority * len(tracked)
    _total_strong_or_generated = total_existing_strong + total_generated
    _total_sufficient = _total_strong_or_generated >= _total_needed

    by_priority: Dict[str, Dict] = {}
    for p in tracked:
        ex = existing_counts[p]
        gen = generated_counts[p]
        if _total_sufficient:
            pct = 100  # Overall coverage sufficient — don't penalize uneven priority distribution
        elif ex >= min_per_priority or gen > 0:
            pct = 100
        elif ex > 0:
            pct = min(99, int(ex / min_per_priority * 100))
        else:
            soft = soft_counts[p]
            pct = min(99, int(soft / min_per_priority * 100)) if soft > 0 else 0
        by_priority[p] = {"existing": ex, "generated": gen, "needed": min_per_priority, "pct": pct}

    if tracked:
        all_covered = all(by_priority[p]["pct"] == 100 for p in tracked)
        # Only truly "uncovered" when no related tests found at all and nothing generated
        nothing = len(related_tests) == 0 and total_generated == 0
        if nothing:
            status = "uncovered"
            final_pct = 0
        elif all_covered:
            status = "covered"
            final_pct = 100
        else:
            status = "partially_covered"
            covered_count = sum(1 for p in tracked if by_priority[p]["pct"] == 100)
            raw_pct = int(covered_count / len(tracked) * 100)
            if raw_pct == 0 and len(related_tests) > 0:
                # Tests found but none matched a tracked priority (e.g. priority=None/N/A).
                # Show a soft % based on total related tests vs total needed across all priorities.
                total_needed = min_per_priority * len(tracked)
                raw_pct = min(99, int(len(related_tests) / total_needed * 100))
            final_pct = min(99, raw_pct)
        ex_only_pct = min(100, sum(
            100 if existing_counts[p] >= min_per_priority
            else (min(99, int(existing_counts[p] / min_per_priority * 100)) if existing_counts[p] > 0 else 0)
            for p in tracked
        ) // len(tracked))
    else:
        final_pct = ex_only_pct = 0
        status = "uncovered"

    return {
        "final_coverage_pct": final_pct,
        "existing_coverage_pct": ex_only_pct,
        "status": status,
        "by_priority": by_priority,
        "total_existing_strong": total_existing_strong,
        "total_generated": total_generated,
    }


def _load_env():
    """Load .env for config."""
    env_path = _ROOT / "config" / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_path)
        except Exception:
            pass


def _extract_text_from_file(file_path: Path) -> str:
    """Extract plain text from PDF, DOCX, or TXT file."""
    path = Path(file_path)
    suffix = path.suffix.lower()
    text = ""

    if suffix == ".txt":
        text = path.read_text(encoding="utf-8", errors="replace")
    elif suffix == ".pdf":
        try:
            from langchain_community.document_loaders import PyPDFLoader
            loader = PyPDFLoader(str(path))
            docs = loader.load()
            text = "\n".join(d.page_content for d in docs)
        except ImportError:
            raise ImportError("PyPDFLoader required for PDF. Install: pip install pypdf langchain-community")
    elif suffix == ".docx":
        try:
            from docx import Document as DocxDocument
            doc = DocxDocument(str(path))
            text = "\n".join(p.text for p in doc.paragraphs)
        except ImportError:
            raise ImportError("python-docx required for Word. Install: pip install python-docx")
    elif suffix == ".doc":
        # .doc is the old binary format, python-docx doesn't support it
        # Try textract first, fall back to antiword, then error with helpful message
        try:
            import textract
            text = textract.process(str(path)).decode("utf-8", errors="replace")
        except ImportError:
            # textract not installed, try antiword
            try:
                import subprocess
                result = subprocess.run(
                    ["antiword", str(path)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode == 0:
                    text = result.stdout
                else:
                    # antiword ran but failed - include actual error message
                    error_detail = result.stderr.strip() if result.stderr else "Unknown error"
                    raise ValueError(
                        f"Failed to read .doc file: {error_detail}. "
                        f"The file may be corrupted or not a valid Word document. "
                        f"Try saving the file as .docx format."
                    )
            except FileNotFoundError:
                raise ValueError(
                    f"Cannot read .doc file. The old .doc format requires 'antiword'. "
                    f"Install: brew install antiword (macOS) or apt install antiword (Linux)"
                )
        except Exception as e:
            # textract is installed but failed
            raise ValueError(f"Failed to read .doc file: {str(e)}. Try converting to .docx format.")
    else:
        raise ValueError(f"Unsupported file type: {suffix}. Use .txt, .pdf, .docx, or .doc")

    return text.strip()


def _fetch_confluence_page(page_url: str) -> str:
    """Fetch Confluence page content from URL."""
    from backend.rag.rag_settings import get_config
    _load_env()
    config = get_config()
    url = getattr(config, "confluence_url", None) or os.getenv("CONFLUENCE_URL", "")
    email = getattr(config, "confluence_email", None) or os.getenv("CONFLUENCE_EMAIL", "")
    api_token = getattr(config, "confluence_api_token", None) or os.getenv("CONFLUENCE_API_TOKEN", "")
    if not url or not email or not api_token:
        raise ValueError("Confluence not configured. Set CONFLUENCE_URL, CONFLUENCE_EMAIL, CONFLUENCE_API_TOKEN in .env")
    from backend.connectors.confluence_connector import ConfluenceConnector
    connector = ConfluenceConnector(url=url, email=email, api_token=api_token)
    page = connector.get_page_from_url(page_url)
    return (page.get("title", "") + "\n\n" + page.get("body", "")).strip()


class RequirementAnalysisService:
    """Service for analyzing requirement specs against existing tests."""

    def __init__(self, rag_service: Optional[Any] = None):
        _load_env()
        self.rag_service = rag_service
        if rag_service is None:
            from backend.services.rag_service import RAGService
            self.rag_service = RAGService()

    def parse_input(
        self,
        text: Optional[str] = None,
        file_path: Optional[Path] = None,
        confluence_url: Optional[str] = None,
    ) -> str:
        """
        Parse requirement spec from one of: pasted text, file upload, or Confluence URL.

        Args:
            text: Pasted requirement spec text
            file_path: Path to uploaded file (PDF, DOCX, TXT)
            confluence_url: Confluence page URL

        Returns:
            Raw spec text
        """
        if text and text.strip():
            return text.strip()
        if file_path:
            path = Path(file_path)
            if not path.exists():
                raise FileNotFoundError(f"File not found: {path}")
            return _extract_text_from_file(path)
        if confluence_url and confluence_url.strip():
            return _fetch_confluence_page(confluence_url.strip())
        raise ValueError("Provide one of: text, file_path, or confluence_url")

    def _collect_requirements_from_sources(
        self,
        text: Optional[str] = None,
        file_paths: Optional[List[Path]] = None,
        confluence_urls: Optional[List[str]] = None,
    ) -> tuple:
        """
        Load requirements from multiple sources independently, merge and renumber them.
        Returns (requirements_list, combined_spec_text) where combined_spec_text is used
        for doc_summary_callback only.
        """
        from backend.extractors.requirement_extractor import extract_requirements as _extract_reqs
        all_reqs: List[Dict[str, Any]] = []
        spec_parts: List[str] = []
        counter = 1

        if text and text.strip():
            reqs = _extract_reqs(text.strip())
            for r in reqs:
                r["id"] = f"REQ-{counter}"; counter += 1
            all_reqs.extend(reqs)
            spec_parts.append(text.strip())

        for fp in (file_paths or []):
            spec = _extract_text_from_file(fp)
            reqs = _extract_reqs(spec)
            for r in reqs:
                r["id"] = f"REQ-{counter}"; counter += 1
            all_reqs.extend(reqs)
            spec_parts.append(f"=== File: {Path(fp).name} ===\n{spec}")

        for url in (confluence_urls or []):
            spec = _fetch_confluence_page(url.strip())
            reqs = _extract_reqs(spec)
            for r in reqs:
                r["id"] = f"REQ-{counter}"; counter += 1
            all_reqs.extend(reqs)
            _page_title = spec.split("\n")[0][:80].strip() or url
            spec_parts.append(f"=== Confluence: {_page_title} ===\n{spec}")

        combined_text = "\n\n---\n\n".join(spec_parts)
        return all_reqs, combined_text

    def analyze(
        self,
        text: Optional[str] = None,
        file_path: Optional[Path] = None,
        confluence_url: Optional[str] = None,
        file_paths: Optional[List[Path]] = None,
        confluence_urls: Optional[List[str]] = None,
        generate_new_tests: bool = True,
        generate_p2_p3_tests: bool = False,
        push_to_testrail: bool = False,
        target_section_id: Optional[int] = None,
        use_section_of_related: bool = False,
        progress_callback: Optional[Any] = None,
        requirement_result_callback: Optional[Any] = None,
        doc_summary_callback: Optional[Any] = None,
        requirement_step_callback: Optional[Any] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> Dict[str, Any]:
        """
        Run full requirement analysis pipeline.

        Args:
            text: Pasted requirement spec text
            file_path: Path to uploaded file
            confluence_url: Confluence page URL
            generate_new_tests: Whether to generate new tests for uncovered requirements
            generate_p2_p3_tests: If True, generated tests may include P2/P3 (default: P0/P1 only)
            push_to_testrail: If True and generated_tests exist, push them to TestRail (requires config)
            target_section_id: TestRail section ID for push (overrides config default)
            use_section_of_related: If True, push each requirement's generated tests into the section of its first related test; fallback to target_section_id when no related tests
            progress_callback: Optional callable(stage: int, message: str, progress: float 0-1) for UI progress
            requirement_result_callback: Optional callable(req_id: str, data: dict) called after each requirement is done (for streaming UI)

        Returns:
            Analysis result: requirements_analyzed, related_tests, tests_needing_update,
            uncovered_requirements, generated_tests, e2e_workflow_tests, summary, pushed_to_testrail (if any)
        """
        last_progress = [0.0]  # use list so report() can update
        run_id = uuid.uuid4().hex
        run_cost = [0.0]  # total estimated cost for this run (USD)

        def report(stage: int, message: str, progress: float) -> None:
            if progress_callback:
                try:
                    p = max(last_progress[0], min(1.0, progress))
                    last_progress[0] = p
                    progress_callback(stage, message, p)
                except Exception:
                    pass

        from backend.rag.rag_settings import get_config
        config = get_config()
        report(1, "Analysing requirements", 0.05)

        # Normalize legacy single params + new list params into unified lists
        _file_paths_all = list(filter(None, ([file_path] if file_path else []) + (file_paths or [])))
        _confluence_urls_all = list(filter(None, ([confluence_url] if confluence_url else []) + (confluence_urls or [])))
        _is_multi = len(_file_paths_all) > 1 or len(_confluence_urls_all) > 1 or (
            sum([bool(text), bool(_file_paths_all), bool(_confluence_urls_all)]) > 1
        )

        if _is_multi:
            print(f"[Multi-source] Loading from {len(_file_paths_all)} file(s) + {len(_confluence_urls_all)} URL(s)")
            requirements, spec_text = self._collect_requirements_from_sources(
                text=text, file_paths=_file_paths_all, confluence_urls=_confluence_urls_all,
            )
            print(f"[Multi-source] Loaded {len(requirements)} requirements total")
            report(1, "Analysing requirements", 0.25)
        else:
            # Single-source path (unchanged behaviour)
            spec_text = self.parse_input(
                text=text,
                file_path=_file_paths_all[0] if _file_paths_all else None,
                confluence_url=_confluence_urls_all[0] if _confluence_urls_all else None,
            )
            report(1, "Analysing requirements", 0.15)

            # Cache key: SHA-256 of the first 50k chars of spec_text.
            # Same document → same cache hit → identical requirements across runs → deterministic scores.
            _spec_hash = hashlib.sha256(spec_text[:50000].encode("utf-8", errors="replace")).hexdigest()
            if _spec_hash in _requirements_cache:
                requirements = copy.deepcopy(_requirements_cache[_spec_hash])
                print(f"[analyze] Requirements cache hit ({len(requirements)} reqs) for spec hash {_spec_hash[:12]}")
            else:
                requirements = extract_requirements(spec_text)
                # Optionally enrich requirement titles with document context using LLM
                enrich_with_context = os.getenv("REQUIREMENT_ENRICH_WITH_CONTEXT", "true").lower() in ("true", "1", "yes")
                if enrich_with_context and requirements and self.rag_service and self.rag_service.rag:
                    rag = self.rag_service.rag
                    if rag.llm:
                        report(1, "Enriching requirements with document context", 0.20)
                        requirements = enrich_requirements_with_context(requirements, spec_text, rag.llm)
                        # Also clean up descriptions to remove raw HTML, API specs, internal notes
                        report(1, "Cleaning up requirement descriptions", 0.22)
                        requirements = clean_descriptions_with_llm(requirements, rag.llm)
                _cache_put(_spec_hash, copy.deepcopy(requirements))
                print(f"[analyze] Requirements cached ({len(requirements)} reqs) for spec hash {_spec_hash[:12]}")
        
        report(1, "Summarising document", 0.25)

        # Run doc summary in a background thread so it doesn't block the per-req loop.
        # The LLM call for summary is independent of requirement analysis.
        if doc_summary_callback:
            def _run_doc_summary():
                try:
                    lines = [l for l in spec_text.splitlines() if l.strip()]
                    _title = lines[0][:200].strip() if lines else "Untitled"
                    _word_count = len(spec_text.split())
                    _source_type = "multi" if _is_multi else ("confluence" if _confluence_urls_all else ("file" if _file_paths_all else "text"))
                    _req_titles = "\n".join(
                        f"- {r.get('id', '')}: {r.get('title', '')}" for r in requirements[:20]
                    )
                    _ai_summary = None
                    _llm = self.rag_service.rag.llm if (self.rag_service and self.rag_service.rag) else None
                    if _llm:
                        from langchain_core.prompts import ChatPromptTemplate
                        _summary_prompt = ChatPromptTemplate.from_messages([
                            ("system", (
                                "You are a technical analyst. Given a requirements document, produce a concise summary.\n\n"
                                "Return ONLY a markdown-formatted summary with:\n"
                                "- A one-sentence overview of what the document covers\n"
                                "- A short bulleted list of the key requirements or features (max 6 bullets)\n"
                                "- Any important constraints, edge cases, or dependencies (if present)\n\n"
                                "Keep it short and clear. Use plain language. No preamble."
                            )),
                            ("human", "Document title: {title}\n\nRequirements identified:\n{req_titles}\n\nDocument content (truncated):\n{content}\n\nSummary:"),
                        ])
                        _chain = _summary_prompt | _llm
                        _result = _chain.invoke({
                            "title": _title,
                            "req_titles": _req_titles or "(none extracted)",
                            "content": spec_text[:4000],
                        })
                        _ai_summary = (_result.content if hasattr(_result, "content") else str(_result)).strip()
                    doc_summary_callback({
                        "source_type": _source_type,
                        "title": _title,
                        "summary": _ai_summary,
                        "word_count": _word_count,
                        "requirement_count": len(requirements),
                        "confluence_url": confluence_url or "",
                    })
                except Exception:
                    pass
            threading.Thread(target=_run_doc_summary, daemon=True, name="doc-summary").start()

        related_specs_per_req: Dict[str, List[Dict]] = {}
        related_tests: Dict[str, List[Dict]] = {}
        tests_needing_update: Dict[str, List[Dict]] = {}
        tests_ok: Dict[str, List[str]] = {}
        coverage_gap_reason_per_req: Dict[str, str] = {}
        uncovered_requirements: List[str] = []
        coverage_per_req: Dict[str, Dict] = {}
        generated_tests: Dict[str, List[Dict]] = {}
        # Related tests: filtered by REQUIREMENT_TESTS_SIMILARITY_THRESHOLD in RAG (similarity_score).
        # Need-update band: tests with similarity >= retrieval_threshold AND < coverage_min_similarity go to
        # "Need update" tab; tests with similarity >= coverage_min_similarity go to "Reuse as-is".
        retrieval_threshold_pct = getattr(config, "requirement_tests_similarity_threshold", 50.0)
        needs_update_similarity_ceiling_pct = float(os.getenv("REQUIREMENT_TESTS_COVERAGE_MIN_SIMILARITY", "80"))

        total_reqs = len(requirements)
        # Extract source Confluence page IDs (all input URLs) so we can exclude them from
        # find_related_specs() — pages loaded as the requirement source should not appear as
        # their own "supporting context" (near-100% similarity, no added value).
        _source_confluence_page_ids: set = set()
        for _cu in _confluence_urls_all:
            _cpage_m = re.search(r'/pages/(\d+)(?:/|$)', _cu)
            if _cpage_m:
                _source_confluence_page_ids.add(_cpage_m.group(1))
        # Keep legacy single-id alias for the filter below
        _source_confluence_page_id: Optional[str] = next(iter(_source_confluence_page_ids), None)
        if _source_confluence_page_ids:
            print(f"[Specs] Source Confluence page IDs: {_source_confluence_page_ids} — will be excluded from related specs")
        # Single "Fetching context" stage: Confluence + TestRail per requirement (stage 2).
        # Use a canonical requirement string for retrieval so it matches exactly how we store
        # test chunks (Requirement: REQ-ID: Title\nDescription). Same format = same embedding = generated tests are found.
        # Load the vectorstore once for the entire loop to avoid repeated disk reads per requirement
        _session_vectorstore = self.rag_service.load_fresh_vectorstore_once()
        # Per-requirement acceptance criteria extracted by LLM (used by Gate 1, Gate 2, and generation)
        acceptance_criteria_per_req: Dict[str, List[str]] = {}
        report(2, "Fetching context from Confluence & TestRail (0 of %d requirements)" % total_reqs if total_reqs else "Fetching context from Confluence & TestRail", 0.30)

        # --- Per-requirement parallel processing ---
        # Each requirement is independent: find specs → extract ACs → find tests →
        # assess → gate2 → generate. Running them in parallel cuts wall-clock time
        # from (N × ~80s) to (~80s) regardless of requirement count.
        _req_lock = threading.Lock()
        _completed_reqs = [0]

        def _process_one_req(idx: int, req: Dict) -> None:
            _start_time = time.time()

            def _cancelled() -> bool:
                return cancel_event is not None and cancel_event.is_set()

            def _emit_step(name: str) -> None:
                if requirement_step_callback:
                    try:
                        requirement_step_callback(req_id, name)
                    except Exception:
                        pass

            req_id = req.get("id", "")
            req_title = (req.get("title") or "").strip()
            req_desc = (req.get("description") or "").strip()
            # Canonical format must match chunk prefix in ChromaDB so retrieval finds generated tests
            _body = (f"{req_id}: " if req_id else "") + req_title + ("\n" + req_desc if req_desc else "")
            req_text = ("Requirement: " + _body.strip()) if _body.strip() else ""
            _desc_snippet = req_desc[:150].strip() if req_desc else ""
            _retrieval_query = (
                f"Requirement: {(req_id + ': ') if req_id else ''}{req_title}"
                + (f"\n{_desc_snippet}" if _desc_snippet else "")
            )
            if _cancelled():
                return
            # 1. Retrieve Confluence specs first (basic query, before ACs are known) so they can
            #    enrich AC extraction with domain context documented in Confluence.
            _emit_step("Finding related specs")
            specs = self.rag_service.find_related_specs(_retrieval_query, k=10)
            if _source_confluence_page_ids:
                _before = len(specs)
                specs = [
                    s for s in specs
                    if s.get("page_id") not in _source_confluence_page_ids
                    and not any(f"/pages/{_pid}/" in (s.get("url") or "") for _pid in _source_confluence_page_ids)
                    and not any(f"/pages/{_pid}/" in (s.get("content") or "") for _pid in _source_confluence_page_ids)
                ]
                if len(specs) < _before:
                    print(f"[Specs] {req_id}: excluded {_before - len(specs)} chunk(s) from source page(s) {_source_confluence_page_ids}")
            related_specs_per_req[req_id] = specs
            print(f"[Specs] {req_id}: {len(specs)} Confluence chunk(s) retrieved — now extracting ACs with Confluence context")
            if _cancelled():
                return
            # 2. Extract acceptance criteria with Confluence specs as context: drives Gate 1 threshold,
            #    Gate 2 prompt, and generation target count. Confluence docs may surface ACs not written
            #    in the requirement ticket itself.
            _emit_step("Extracting acceptance criteria")
            _high_sim_specs = [s for s in specs if (s.get("similarity_score") or 0) >= 0.65]
            _acs = self._extract_acceptance_criteria(req_title, req_desc, specs_context=_high_sim_specs, run_id=run_id, run_cost=run_cost)
            acceptance_criteria_per_req[req_id] = _acs
            if _cancelled():
                return
            # 3. Augment retrieval query with ACs so vector search finds tests that cover specific criteria,
            #    not just the high-level title. Cap at 4 ACs and 120 chars each to keep query focused.
            _emit_step("Finding related tests")
            if _acs:
                _ac_snippet = "; ".join(ac[:120] for ac in _acs[:4])
                _retrieval_query += f"\nAcceptance criteria: {_ac_snippet}"
            _retrieval_k = 15 if generate_p2_p3_tests else 10
            tests = self.rag_service.find_related_tests(_retrieval_query, k=_retrieval_k, vectorstore=_session_vectorstore)
            related_tests[req_id] = tests

            if tests:
                # LLM batch-assessment of all retrieved tests: determines per-test relevance and update need.
                # Falls back to similarity-band logic if the batch call fails.
                _emit_step("Assessing test coverage")
                print(f"[assess-batch] {req_id}: reviewing all {len(tests)} test(s) with LLM...")
                _batch_assessments = self._assess_all_tests_batch(req_text, tests, run_id=run_id, run_cost=run_cost)

                if _batch_assessments:
                    _assess_map: Dict[str, Dict] = {}
                    for i, a in enumerate(_batch_assessments):
                        _tid = a.get("testrail_id") or (tests[i].get("testrail_id") if i < len(tests) else None)
                        if _tid:
                            _assess_map[str(_tid)] = a

                    _ok_ids: List[str] = []
                    _needing: List[Dict] = []
                    _irrelevant_count = 0

                    for t in tests:
                        _tid = str(t.get("testrail_id") or "N/A")
                        _a = _assess_map.get(_tid, {})
                        _status = (_a.get("status") or "relevant_ok").lower()

                        # Check for blank content FIRST — regardless of LLM classification.
                        # Blank tests should always show suggestions, even if LLM said "irrelevant".
                        _content = (t.get("content") or "")
                        _meta_steps = (t.get("steps") or "").strip()
                        _meta_expected = (t.get("expected_result") or "").strip()
                        _meta_precond = (t.get("preconditions") or "").strip()
                        _is_blank = not _meta_steps and not _meta_expected and not _meta_precond and "step" not in _content.lower()

                        if _is_blank:
                            _needing.append({**t, "status": "needs_update",
                                              "suggested_changes": ["Add detailed test steps", "Add expected results", "Add preconditions"],
                                              "reason": "Test case has no steps, expected results, or preconditions — needs content to be actionable",
                                              "confidence": 0.9})
                        elif _status == "irrelevant":
                            _irrelevant_count += 1
                            continue  # Excluded from coverage and from update list
                        elif _status in ("needs_update", "partial"):
                            _needing.append({**t, "status": _status,
                                              "suggested_changes": _a.get("suggested_changes") or [],
                                              "reason": _a.get("reason") or "",
                                              "confidence": _a.get("confidence") or 0.0})
                        else:  # relevant_ok
                            _ok_ids.append(_tid)

                    tests_ok[req_id] = _ok_ids
                    tests_needing_update[req_id] = _needing
                    if _irrelevant_count:
                        print(f"[assess-batch] {req_id}: {len(_ok_ids)} ok, {len(_needing)} need update, {_irrelevant_count} irrelevant (excluded)")
                    else:
                        print(f"[assess-batch] {req_id}: {len(_ok_ids)} ok, {len(_needing)} need update")
                else:
                    # Fallback: similarity bands (used if batch LLM call fails)
                    print(f"[assess-batch] {req_id}: batch failed — falling back to similarity bands")
                    def _norm_similarity_pct(t: Dict) -> float:
                        s = t.get("similarity_score")
                        if s is None:
                            return -1.0
                        return (s * 100.0) if s <= 1.0 else float(s)
                    need_update_band = []
                    reuse_band = []
                    for t in tests:
                        spct = _norm_similarity_pct(t)
                        if spct < 0:
                            reuse_band.append(t)
                            continue
                        if retrieval_threshold_pct <= spct <= needs_update_similarity_ceiling_pct:
                            need_update_band.append(t)
                        else:
                            reuse_band.append(t)
                    tests_ok[req_id] = [t.get("testrail_id") or "N/A" for t in reuse_band]
                    _ASSESS_CAP = 3
                    assess_band = need_update_band[:_ASSESS_CAP]
                    skip_band = need_update_band[_ASSESS_CAP:]
                    if skip_band:
                        tests_ok[req_id].extend(t.get("testrail_id") or "N/A" for t in skip_band)
                    needing_from_llm, ok_ids_from_band = self._assess_updates(req_text, assess_band, run_id=run_id, run_cost=run_cost)
                    tests_needing_update[req_id] = [e for e in needing_from_llm if (e.get("status") or "ok") != "ok"]
                    tests_ok[req_id].extend(ok_ids_from_band)

                # --- GATE 1: per-priority count check (deterministic) ---
                _all_test_ids = [str(t.get("testrail_id") or "") for t in tests if t.get("testrail_id")]
                generate_priorities = _compute_generate_priorities(
                    tests, generate_p2_p3_tests, ok_ids=_all_test_ids,
                )

                # --- GATE 2: LLM content coverage check (always runs when tests exist) ---
                gate2_sufficient = False
                gate2_reason = ""
                if tests:
                    _emit_step("Checking coverage gap")
                    gate2_sufficient, gate2_reason = self._is_coverage_sufficient(
                        req_text, tests,
                        acceptance_criteria=acceptance_criteria_per_req.get(req_id),
                        specs_context=(related_specs_per_req.get(req_id) or [])[:2],
                        run_id=run_id, run_cost=run_cost,
                    )

                # --- DECISION: 4-way matrix ---
                _g1_pass = not generate_priorities
                _g2_pass = gate2_sufficient

                if _g1_pass and _g2_pass:
                    # Both pass: enough tests per priority AND content covers ACs
                    print(f"[DECISION] {req_id}: Gate1 PASS, Gate2 PASS ({gate2_reason}) → skipping generation")

                elif _g1_pass and not _g2_pass:
                    # Enough tests by count but LLM found content gaps.
                    # Trust Gate 1 — don't generate. Show Gate 2's feedback as suggestions instead.
                    # This prevents infinite re-generation when the LLM keeps finding new uncovered ACs.
                    _g2_label = f"Gate2 LLM FAIL ({gate2_reason})" if tests else "Gate2 SKIPPED"
                    print(f"[DECISION] {req_id}: Gate1 PASS, {_g2_label} → not generating (Gate1 sufficient, showing suggestions only)")
                    # Add the uncovered ACs as suggestions on existing tests
                    if gate2_reason and tests_needing_update.get(req_id):
                        tests_needing_update[req_id][0].setdefault("suggested_changes", []).append(
                            f"Coverage gap noted by AI: {gate2_reason}"
                        )

                elif not _g1_pass and _g2_pass:
                    # Content is covered but priority distribution is uneven
                    # → suggest priority changes on existing tests instead of generating new ones
                    _missing = ", ".join(generate_priorities)
                    print(f"[DECISION] {req_id}: Gate1 FAIL ({_missing}), Gate2 PASS ({gate2_reason}) → suggesting priority updates (not generating)")
                    # Add priority suggestions to needs-update tests
                    for _t in tests:
                        _tp = (_t.get("priority") or "").strip().upper()
                        _tid = str(_t.get("testrail_id") or "")
                        if _tp and _tp not in generate_priorities and _tid:
                            # This test has a priority that's already covered — suggest changing to an under-covered one
                            for _nu in tests_needing_update.get(req_id, []):
                                if str(_nu.get("testrail_id") or "") == _tid:
                                    _nu.setdefault("suggested_changes", []).append(
                                        f"Consider changing priority from {_tp} to {generate_priorities[0]} (under-covered)"
                                    )
                                    break
                    generate_priorities = []  # Don't generate — just suggest

                else:
                    # Both fail: real content gaps + priority gaps → generate
                    _g1_detail = ", ".join(generate_priorities)
                    _g2_label = f"Gate2 LLM FAIL ({gate2_reason})" if tests else "Gate2 SKIPPED (0 tests)"
                    print(f"[DECISION] {req_id}: Gate1 FAIL ({_g1_detail}), {_g2_label} → generating {', '.join(generate_priorities)}")

                # Generate if priorities still need coverage
                if generate_priorities:
                    coverage_gap_reason_per_req[req_id] = f"Need more tests for: {', '.join(generate_priorities)}."
                    with _req_lock:
                        uncovered_requirements.append(req_id)
                    if generate_new_tests:
                        _emit_step("Generating tests")
                        report(3, "Generating tests", 0.60)
                        gen_list = self._generate_tests_for_requirement(
                            req, tests,
                            specs_context=related_specs_per_req.get(req_id, []),
                            reuse_test_ids=tests_ok.get(req_id, []),
                            update_test_infos=tests_needing_update.get(req_id, []),
                            coverage_gap_reason=coverage_gap_reason_per_req.get(req_id, ""),
                            generate_p2_p3=generate_p2_p3_tests,
                            allowed_priorities=generate_priorities,
                            acceptance_criteria=acceptance_criteria_per_req.get(req_id),
                            run_id=run_id, run_cost=run_cost,
                        )
                        if gen_list:
                            generated_tests[req_id] = gen_list
            else:
                tests_needing_update[req_id] = []
                tests_ok[req_id] = []
                coverage_gap_reason_per_req[req_id] = "No related tests."
                # No related tests at all → generate P0/P1 (and optionally P2/P3)
                with _req_lock:
                    uncovered_requirements.append(req_id)
                all_priorities = ["P0", "P1"] + (["P2", "P3"] if generate_p2_p3_tests else [])
                if generate_new_tests:
                    _emit_step("Generating tests")
                    report(3, "Generating tests", 0.60)
                    gen_list = self._generate_tests_for_requirement(
                        req, tests,
                        specs_context=related_specs_per_req.get(req_id, []),
                        reuse_test_ids=[],
                        update_test_infos=[],
                        coverage_gap_reason=coverage_gap_reason_per_req.get(req_id, ""),
                        generate_p2_p3=generate_p2_p3_tests,
                        allowed_priorities=all_priorities,
                        acceptance_criteria=acceptance_criteria_per_req.get(req_id),
                        run_id=run_id, run_cost=run_cost,
                    )
                    if gen_list:
                        generated_tests[req_id] = gen_list

            _coverage = _compute_coverage_metrics(
                related_tests=related_tests[req_id],
                generated_tests_for_req=generated_tests.get(req_id, []),
                generate_p2_p3=generate_p2_p3_tests,
                ok_ids=tests_ok.get(req_id, []),
            )
            coverage_per_req[req_id] = _coverage

            # Report progress and stream result under the lock to keep counter consistent
            with _req_lock:
                _completed_reqs[0] += 1
                _done = _completed_reqs[0]
            report(2, f"Analyzed {_done} of {total_reqs} requirements", 0.30 + 0.60 * _done / max(1, total_reqs))

            if requirement_result_callback:
                try:
                    requirement_result_callback(
                        req_id,
                        {
                            "requirement": req,
                            "related_tests": related_tests[req_id],
                            "tests_needing_update": tests_needing_update[req_id],
                            "tests_ok": tests_ok[req_id],
                            "generated_tests": generated_tests.get(req_id, []),
                            "uncovered": req_id in uncovered_requirements,
                            "related_specs": related_specs_per_req.get(req_id, []),
                            "coverage": _coverage,
                            "acceptance_criteria": acceptance_criteria_per_req.get(req_id, []),
                            "elapsed_s": round(time.time() - _start_time, 1),
                        },
                    )
                except Exception:
                    pass

        # Check parallel processing setting (Admin Settings → Requirements → Parallel Processing)
        _parallel_enabled = os.getenv("REQUIREMENT_PARALLEL_PROCESSING", "true").lower() in ("true", "1", "yes")

        def _run_with_sem(idx: int, req: Dict) -> None:
            with _llm_concurrency_sem:
                _process_one_req(idx, req)

        if _parallel_enabled and len(requirements) > 1:
            _max_parallel = min(len(requirements), _LLM_SEM_VALUE)
            print(f"[analyze] Processing {len(requirements)} requirement(s) with {_max_parallel} parallel worker(s)")
            with ThreadPoolExecutor(max_workers=_max_parallel) as _executor:
                _futures = {
                    _executor.submit(_run_with_sem, idx, req): req.get("id", f"req-{idx}")
                    for idx, req in enumerate(requirements)
                }
                for _future in as_completed(_futures):
                    _req_label = _futures[_future]
                    try:
                        _future.result()
                    except Exception as _exc:
                        print(f"[analyze] {_req_label} failed: {_exc}")
        else:
            print(f"[analyze] Processing {len(requirements)} requirement(s) sequentially")
            for idx, req in enumerate(requirements):
                if cancel_event is not None and cancel_event.is_set():
                    break
                _process_one_req(idx, req)

        # Collect existing E2E tests from related_tests (always, regardless of generate_new_tests flag).
        # Primary detection: case_type == "FCT / Regression" (stored in ChromaDB metadata after sync).
        # Fallback for tests indexed before this field was added: "e2e" in title or "type: fct" in content.
        # Only include tests above REQUIREMENT_TESTS_COVERAGE_MIN_SIMILARITY to avoid weak matches.
        _e2e_min_sim = 60.0
        try:
            v = os.getenv("REQUIREMENT_TESTS_COVERAGE_MIN_SIMILARITY", "").strip()
            if v:
                _e2e_min_sim = max(0.0, min(100.0, float(v)))
        except (ValueError, TypeError):
            pass
        existing_e2e_tests: List[Dict[str, Any]] = []
        _seen_e2e_ids: set = set()
        for _tests in related_tests.values():
            for _t in _tests:
                _tid = _t.get("testrail_id") or ""
                _sim = _t.get("similarity_score") or 0.0
                # similarity_score is stored as 0-1 float; compare against pct threshold
                _sim_pct = _sim * 100.0 if _sim <= 1.0 else _sim
                if _sim_pct < _e2e_min_sim:
                    continue
                _case_type = (_t.get("case_type") or "").lower()
                _title = (_t.get("title") or "").lower()
                _content = (_t.get("content") or "").lower()
                _is_e2e = (
                    "fct / regression" in _case_type
                    or _title.startswith("e2e:")
                    or "type: fct / regression" in _content
                )
                if _is_e2e and _tid not in _seen_e2e_ids:
                    _seen_e2e_ids.add(_tid)
                    existing_e2e_tests.append(_t)

        # E2E Workflow Test Generation: LLM-gated (always ask the LLM if existing E2E coverage suffices)
        e2e_workflow_tests: List[Dict[str, Any]] = []
        _n_existing_e2e = len(existing_e2e_tests)
        print(f"[E2E DECISION] existing={_n_existing_e2e}, generate_new_tests={generate_new_tests}")

        if not generate_new_tests or len(requirements) < 1:
            print(f"[E2E DECISION] Skipped — generate_new_tests={generate_new_tests}")
        else:
            # Deterministic gate: if enough existing E2E tests (≥ total requirements), skip LLM check.
            # This prevents non-deterministic LLM from re-generating after user already pushed E2E tests.
            _e2e_gate_sufficient = False
            _e2e_gate_reason = ""
            if _n_existing_e2e >= len(requirements):
                _e2e_gate_sufficient = True
                _e2e_gate_reason = f"{_n_existing_e2e} existing E2E tests >= {len(requirements)} requirements"
            elif existing_e2e_tests:
                _e2e_gate_sufficient, _e2e_gate_reason = self._is_e2e_coverage_sufficient(
                    requirements, existing_e2e_tests, run_id=run_id, run_cost=run_cost
                )

            if _e2e_gate_sufficient:
                print(f"[E2E DECISION] LLM PASS ({_e2e_gate_reason}) — {_n_existing_e2e} existing E2E tests sufficient → skipping generation")
            else:
                _gate_label = "No existing E2E tests" if not existing_e2e_tests else f"LLM FAIL ({_e2e_gate_reason})"
                print(f"[E2E DECISION] {_gate_label} → generating E2E tests")
                report(3, "Identifying E2E workflows", 0.85)
                print("[E2E] Fetching broad critical product tests for regression context...")
                _critical_product_tests = self._fetch_critical_product_tests(k_per_query=8)
                print(f"[E2E] Fetched {len(_critical_product_tests)} critical product tests across product areas")
                workflows = self._identify_e2e_workflows(
                    requirements, related_tests,
                    existing_e2e_tests=existing_e2e_tests,
                    critical_product_tests=_critical_product_tests,
                    run_id=run_id, run_cost=run_cost,
                )
                print(f"[E2E DECISION] Identified {len(workflows)} workflows")
                if workflows:
                    report(3, f"Generating E2E tests for {len(workflows)} workflow(s)", 0.90)
                    batch_e2e = self._generate_e2e_tests_batch(workflows, requirements, run_id=run_id, run_cost=run_cost)
                    for e2e_test in batch_e2e:
                        e2e_workflow_tests.append(e2e_test)
                        print(f"[E2E DECISION] Generated: {e2e_test.get('title', 'no title')}")

        print(f"[E2E DECISION] Total E2E tests generated: {len(e2e_workflow_tests)}")

        report(3, "Generating tests", 1.0)

        # Merge all per-requirement specs into one list (dedupe by url) for API backward compatibility.
        seen_urls: set = set()
        related_specs: List[Dict] = []
        for specs_list in related_specs_per_req.values():
            for s in specs_list:
                url = (s.get("url") or "").strip()
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    related_specs.append(s)
                elif not url:
                    related_specs.append(s)

        pushed: List[Dict] = []
        if push_to_testrail and generated_tests:
            default_section = target_section_id or 0
            push_enabled = getattr(config, "testrail_push_enabled", False) or os.getenv("TESTRAIL_PUSH_ENABLED", "").lower() == "true"
            if push_enabled:
                if use_section_of_related:
                    section_map = self._resolve_sections_from_related(related_tests, default_section)
                    for req_id in generated_tests:
                        section_map.setdefault(req_id, default_section)
                    pushed = self._push_generated_tests_to_testrail(generated_tests, section_map)
                elif default_section:
                    pushed = self._push_generated_tests_to_testrail(generated_tests, default_section)

        _req_coverages = list(coverage_per_req.values())
        _overall_pct = int(sum(c["final_coverage_pct"] for c in _req_coverages) / max(1, len(_req_coverages)))
        _fully_covered_count = sum(1 for c in _req_coverages if c["final_coverage_pct"] == 100)

        return {
            "success": True,
            "requirements_analyzed": len(requirements),
            "requirements": requirements,
            "related_specs": related_specs,
            "related_specs_per_req": related_specs_per_req,
            "related_tests": related_tests,
            "tests_needing_update": tests_needing_update,
            "tests_ok": tests_ok,
            "uncovered_requirements": uncovered_requirements,
            "generated_tests": generated_tests,
            "e2e_workflow_tests": e2e_workflow_tests,
            "existing_e2e_tests": existing_e2e_tests,
            "coverage_gap_reason_per_req": coverage_gap_reason_per_req,
            "coverage_per_req": coverage_per_req,
            "run_id": run_id,
            "total_estimated_cost_usd": round(run_cost[0], 6),
            "pushed_to_testrail": pushed,
            "summary": {
                "total_requirements": len(requirements),
                "requirements_analyzed": len(requirements),
                "requirements_with_coverage": len(requirements) - len(uncovered_requirements),
                "uncovered_count": len(uncovered_requirements),
                "generated_count": len(generated_tests),
                "total_generated_tests": sum(len(v) for v in generated_tests.values()),
                "e2e_workflow_tests_count": len(e2e_workflow_tests),
                "e2e_existing_count": len(existing_e2e_tests),
                "needing_update_count": sum(len(v) for v in tests_needing_update.values()),
                "pushed_count": len(pushed),
                "overall_coverage_pct": _overall_pct,
                "requirements_fully_covered": _fully_covered_count,
                "coverage_min_similarity": _e2e_min_sim,
                "retrieval_similarity_threshold": retrieval_threshold_pct,
            },
        }

    def _assess_all_tests_batch(
        self,
        req_text: str,
        tests: List[Dict],
        run_id: Optional[str] = None,
        run_cost: Optional[List[float]] = None,
    ) -> List[Dict]:
        """
        Batch LLM assessment of ALL retrieved tests for a requirement in a single call.
        Reviews each test individually to determine relevance and update need.
        Status values: "relevant_ok" | "needs_update" | "partial" | "irrelevant"
        Returns empty list on failure so caller can use similarity-band fallback.
        """
        rag = self.rag_service.rag
        if not rag or not rag.llm or not tests:
            return []

        if _llm_delay_sec() > 0:
            time.sleep(_llm_delay_sec())

        from langchain_core.prompts import ChatPromptTemplate

        test_blocks = []
        for i, t in enumerate(tests):
            tid = t.get("testrail_id", f"T{i+1}")
            title = (t.get("title") or "")[:120]
            content = (t.get("content") or t.get("steps") or "")[:400]
            sim = t.get("similarity_score")
            sim_str = f" (similarity: {round(float(sim)*100)}%)" if sim is not None else ""
            test_blocks.append(
                f"TEST {i+1} [{tid}]{sim_str}\nTitle: {title}\nContent: {content}"
            )

        prompt = ChatPromptTemplate.from_messages([
            ("system", """You are a test analyst reviewing existing test cases against a new requirement.

For EACH test, decide:
1. Is it genuinely relevant to this requirement (covers the same feature/flow)?
2. If relevant, is it adequate as-is or does it need updates?

Status options:
- "relevant_ok": Test is relevant and adequately covers the requirement — no changes needed
- "needs_update": Test is relevant but needs specific, limited changes to align with the requirement
- "partial": Test covers only part of the requirement — can be extended with targeted additions
- "irrelevant": Test is about a different feature/flow — not useful for this requirement

Return ONLY a valid JSON array (no markdown), one object per test in the SAME ORDER as input:
[
  {{
    "testrail_id": "<id>",
    "status": "relevant_ok | needs_update | partial | irrelevant",
    "suggested_changes": ["change 1", "change 2"],
    "reason": "brief explanation",
    "confidence": 0.0
  }}
]

Rules:
- suggested_changes must be empty [] for "relevant_ok" and "irrelevant"
- High similarity score alone does not mean relevant — judge by content
- Do NOT flag tests just for being UI vs API focused; only flag real alignment gaps"""),
            ("human", "REQUIREMENT:\n{req_text}\n\nTESTS TO REVIEW:\n{tests}\n\nJSON array ({count} assessments):"),
        ])

        try:
            chain = prompt | rag.llm
            result = chain.invoke({
                "req_text": req_text[:600],
                "tests": "\n\n---\n\n".join(test_blocks),
                "count": len(tests),
            })
            c = record_from_langchain_result("requirement_analysis.assess_all_tests_batch", result, run_id=run_id)
            if run_cost is not None and c is not None:
                run_cost[0] += c
            raw = result.content if hasattr(result, "content") and result.content is not None else str(result)
            if not isinstance(raw, str):
                raw = str(raw) if raw is not None else ""
            match = re.search(r"\[[\s\S]*\]", raw)
            if match:
                assessments = json.loads(match.group())
                print(f"[assess-batch] got {len(assessments)} assessments for {len(tests)} tests")
                return assessments
        except Exception as e:
            print(f"⚠️  _assess_all_tests_batch failed: {e}")
        return []

    def _assess_updates(
        self,
        requirement_text: str,
        related_tests: List[Dict],
        run_id: Optional[str] = None,
        run_cost: Optional[List[float]] = None,
    ) -> tuple:
        """
        LLM: compare requirement vs each related test → status (ok/needs_update/partial), suggested_changes, reason.
        Returns (full_list_one_per_test, ok_ids_from_band). Caller should show as "Need Update" only entries
        with status needs_update/partial; entries with status "ok" belong in "Reuse as-is" (e.g. valid 2FA scenario).
        """
        rag = self.rag_service.rag
        if not rag or not rag.llm or not related_tests:
            return [], [t.get("testrail_id") or "N/A" for t in related_tests]

        needing = []
        ok_ids = []
        from langchain_core.prompts import ChatPromptTemplate

        prompt = ChatPromptTemplate.from_messages([
            ("system", """You are a test analyst. Given a requirement and an existing test, decide if the test needs a small update or is fine/too different.

Return ONLY valid JSON (no markdown): {{"status": "ok"|"needs_update"|"partial", "suggested_changes": ["change1", "change2"], "reason": "brief reason", "confidence": 0.0-1.0}}

Status meanings:
- "ok": Use when (a) the test already aligns with the requirement, OR (b) the test and requirement are so different that updating would mean rewriting the test from scratch. In (b) the user would be better off creating a new test; do NOT show as needing update. Only suggest "needs_update" or "partial" when the test is close and needs targeted, modest edits.
- "needs_update": The test is aligned but needs specific, limited changes (e.g. fix one step, add one assertion, update wording in one place). Suggested_changes must be a short list of concrete, minimal edits—not "rewrite" or "align with requirement".
- "partial": The test covers part of the requirement and can be extended with a few targeted additions. Suggested_changes should be modest (e.g. "Add step for X", "Include expected result for Y").

Important: Tests may be written with UI in mind (e.g. UI steps, user-facing expected results). Do NOT flag a test as needing update just because it includes UI steps or is not "API specific". Do not use reasons like "Test is for API but includes UI step" or "Expected result needs to be API specific." Only flag for real alignment gaps: missing or incorrect steps, wrong expected outcome for the scenario, or outdated wording—not for API vs UI style.

If the existing test and requirement are fundamentally different (different flow, scope, or intent), use status "ok" so we do not show it as "needs update"—otherwise the user would get a random-looking rewrite. Only use needs_update/partial when the test is clearly the same scenario and only needs limited modifications. Keep suggested_changes and reason concise."""),
            ("human", "Requirement:\n{requirement}\n\nExisting test:\n{test_content}\n\nJSON:"),
        ])

        delay = _llm_delay_sec()
        for t in related_tests:
            if delay > 0:
                time.sleep(delay)
            tid = t.get("testrail_id") or t.get("title") or "N/A"
            content = (t.get("title") or "") + "\n" + (t.get("content") or "")[:1500]
            try:
                chain = prompt | rag.llm
                result = chain.invoke({"requirement": requirement_text[:2000], "test_content": content})
                c = record_from_langchain_result("requirement_analysis.assess_updates", result, extra={"testrail_id": tid}, run_id=run_id)
                if run_cost is not None and c is not None:
                    run_cost[0] += c
                raw = result.content if hasattr(result, "content") else str(result)
                match = re.search(r"\{[\s\S]*\}", raw)
                if match:
                    data = json.loads(match.group())
                    status = (data.get("status") or "ok").lower()
                    confidence = float(data.get("confidence", 1.0))
                    suggested = data.get("suggested_changes")
                    if suggested is None or not isinstance(suggested, list):
                        suggested = []
                    reason = (data.get("reason") or "").strip()
                    # Always add to needing so "Update with AI" gets real LLM suggested_changes and reason (not placeholder)
                    needing.append({
                        "testrail_id": tid,
                        "title": (t.get("title") or "")[:200],
                        "content": t.get("content") or "",
                        "similarity_score": t.get("similarity_score"),
                        "status": status,
                        "suggested_changes": suggested,
                        "reason": reason or ("No changes suggested." if status == "ok" else ""),
                        "confidence": confidence,
                    })
                    if status == "ok":
                        ok_ids.append(tid)
                else:
                    ok_ids.append(tid)
                    needing.append({
                        "testrail_id": tid,
                        "title": (t.get("title") or "")[:200],
                        "content": t.get("content") or "",
                        "similarity_score": t.get("similarity_score"),
                        "status": "ok",
                        "suggested_changes": [],
                        "reason": "Could not parse LLM response.",
                        "confidence": 0.0,
                    })
            except Exception as e:
                print(f"⚠️  assess_updates failed for {tid}: {e}")
                ok_ids.append(tid)
                needing.append({
                    "testrail_id": tid,
                    "title": (t.get("title") or "")[:200],
                    "content": t.get("content") or "",
                    "similarity_score": t.get("similarity_score"),
                    "status": "partial",
                    "suggested_changes": ["Review alignment (AI assessment failed)."],
                    "reason": str(e)[:200],
                    "confidence": 0.0,
                })

        return needing, ok_ids

    def _extract_acceptance_criteria(
        self,
        req_title: str,
        req_desc: str,
        specs_context: Optional[List[Dict]] = None,
        run_id: Optional[str] = None,
        run_cost: Optional[List[float]] = None,
    ) -> List[str]:
        """
        LLM: Extract a flat list of testable acceptance criteria from a requirement.
        Returns a list of short AC strings (e.g. ["Valid email required", "Password >= 8 chars"]).
        Falls back to [] on LLM failure — callers must handle the empty case gracefully.
        Skipped if description is very short (< 40 chars) since there's nothing to extract.
        If specs_context (Confluence docs) is provided, they are prepended as feature context so the
        LLM can surface domain-specific ACs not explicitly written in the requirement ticket.
        """
        rag = self.rag_service.rag
        if not rag or not rag.llm:
            return []
        combined = (req_title + ("\n" + req_desc if req_desc else "")).strip()
        if len(combined) < 40:
            return []
        if _llm_delay_sec() > 0:
            time.sleep(_llm_delay_sec())
        from langchain_core.prompts import ChatPromptTemplate

        # Build optional Confluence context block (top 3 specs, 400 chars each)
        _specs_block = ""
        if specs_context:
            _spec_lines = []
            for s in specs_context[:3]:
                _title = s.get("title", "")
                _content = (s.get("content") or "")[:400].strip()
                if _content:
                    _spec_lines.append(f"[{_title}]\n{_content}")
            if _spec_lines:
                _specs_block = "Feature context from Confluence (use to surface additional testable criteria not written in the ticket):\n" + "\n---\n".join(_spec_lines) + "\n\n"

        prompt = ChatPromptTemplate.from_messages([
            ("system", """You are a requirements analyst. Extract all TESTABLE acceptance criteria from the requirement below.

Rules:
- Each AC must be a short, concrete, testable statement (e.g. "Valid email is required", "Duplicate registration is rejected")
- Focus on: validations, business rules, constraints, success conditions, error states
- Use the Confluence context (if provided) to surface domain-specific ACs implied by the feature documentation but not stated in the ticket
- Ignore implementation details, internal notes, or vague statements
- If the requirement is simple (one clear scenario), return 1-2 ACs
- Return ONLY a JSON array of strings, no markdown: ["AC1 text", "AC2 text", ...]
- Maximum 10 ACs. If there are fewer, return fewer."""),
            ("human", "{specs_block}Requirement:\n{requirement}\n\nJSON array of acceptance criteria:"),
        ])
        try:
            chain = prompt | rag.llm
            result = chain.invoke({"requirement": combined[:2000], "specs_block": _specs_block})
            c = record_from_langchain_result("requirement_analysis.extract_acs", result, run_id=run_id)
            if run_cost is not None and c is not None:
                run_cost[0] += c
            raw = result.content if hasattr(result, "content") else str(result)
            arr_match = re.search(r"\[[\s\S]*?\]", raw)
            if arr_match:
                data = json.loads(arr_match.group())
                if isinstance(data, list):
                    acs = [str(x).strip() for x in data if str(x).strip()]
                    if acs:
                        print(f"[AC] Extracted {len(acs)} acceptance criteria: {acs[:3]}{'...' if len(acs) > 3 else ''}")
                        return acs[:10]
        except Exception as e:
            print(f"⚠️  _extract_acceptance_criteria failed: {e}")
        return []

    def _is_coverage_sufficient(
        self,
        requirement_text: str,
        related_tests: List[Dict],
        acceptance_criteria: Optional[List[str]] = None,
        specs_context: Optional[List[Dict]] = None,
        run_id: Optional[str] = None,
        run_cost: Optional[List[float]] = None,
    ) -> Tuple[bool, str]:
        """
        LLM: Do the related tests collectively fully cover the requirement (all acceptance criteria,
        positive and negative E2E flows, critical scenarios)? Return (False, reason) if insufficient.
        Returns (sufficient, reason_string).
        If specs_context (Confluence docs) is provided, the LLM can identify documented behaviors
        not covered by existing tests — treating them as coverage gaps.
        """
        if not related_tests:
            return False, "No related tests."
        rag = self.rag_service.rag
        if not rag or not rag.llm:
            return True, ""  # no LLM: assume sufficient to avoid extra generation
        if _llm_delay_sec() > 0:
            time.sleep(_llm_delay_sec())

        tests_summary = []
        for t in related_tests[:5]:
            title = (t.get("title") or "")[:150]
            content = (t.get("content") or "")[:400]
            tid = t.get("testrail_id") or "N/A"
            tests_summary.append(f"[{tid}] {title}\n{content}")

        from langchain_core.prompts import ChatPromptTemplate
        # Build AC section for the prompt if criteria were extracted
        _ac_section = ""
        if acceptance_criteria:
            _ac_lines = "\n".join(f"  - {ac}" for ac in acceptance_criteria)
            _ac_section = f"\n\nAcceptance Criteria that MUST be covered:\n{_ac_lines}\n\nFor each AC above, check if at least one existing test covers it. List any uncovered ACs in \"uncovered_acs\"."

        # Build Confluence specs section (top 3 specs, 500 chars each)
        _specs_section = ""
        if specs_context:
            _spec_lines = []
            for s in specs_context[:3]:
                _title = s.get("title", "")
                _content = (s.get("content") or "")[:500].strip()
                if _content:
                    _spec_lines.append(f"[{_title}]\n{_content}")
            if _spec_lines:
                _specs_section = "\n\nConfluence documentation for this feature (identify documented behaviors not yet tested):\n" + "\n---\n".join(_spec_lines)

        prompt = ChatPromptTemplate.from_messages([
            ("system", """You are a test analyst. Given one requirement and a list of EXISTING test cases, decide if the tests COLLECTIVELY provide sufficient coverage.

Sufficient coverage means: the tests together cover the key acceptance criteria, important positive and negative flows. Tests that "need update" still count as providing coverage — they exist and cover the scenario, just need minor tweaks.

INSUFFICIENT means: entire flows or acceptance criteria have ZERO test coverage. Do NOT mark as insufficient just because tests need updates — only if important scenarios are completely missing.

Return ONLY valid JSON (no markdown): {{"sufficient": true or false, "uncovered_acs": ["AC text if any uncovered"], "reason": "one short sentence"}}"""),
            ("human", "Requirement:\n{requirement}{ac_section}{specs_section}\n\nExisting related tests:\n{tests}\n\nJSON:"),
        ])
        try:
            chain = prompt | rag.llm
            result = chain.invoke({
                "requirement": requirement_text[:2500],
                "ac_section": _ac_section,
                "specs_section": _specs_section,
                "tests": "\n---\n".join(tests_summary),
            })
            c = record_from_langchain_result("requirement_analysis.coverage_sufficient", result, run_id=run_id)
            if run_cost is not None and c is not None:
                run_cost[0] += c
            raw = result.content if hasattr(result, "content") and result.content is not None else str(result)
            if not isinstance(raw, str):
                raw = str(raw) if raw is not None else ""
            match = re.search(r"\{[\s\S]*\}", raw)
            if match:
                data = json.loads(match.group())
                sufficient = bool(data.get("sufficient", True))
                uncovered = data.get("uncovered_acs") or []
                reason = (data.get("reason") or "").strip()
                # Enrich reason with uncovered ACs for use in generation context
                if uncovered and isinstance(uncovered, list):
                    acs_str = "; ".join(str(a) for a in uncovered[:5])
                    reason = (reason + f" Uncovered ACs: {acs_str}").strip()
                return sufficient, reason
        except Exception as e:
            print(f"⚠️  _is_coverage_sufficient failed: {e}")
        return True, ""  # on parse/LLM failure, assume sufficient to avoid over-generating

    def _is_e2e_coverage_sufficient(
        self,
        requirements: List[Dict[str, Any]],
        existing_e2e_tests: List[Dict],
        run_id: Optional[str] = None,
        run_cost: Optional[List[float]] = None,
    ) -> Tuple[bool, str]:
        """
        LLM: Do the existing E2E tests collectively cover the cross-requirement workflows
        for these requirements? Returns (sufficient, reason).
        Called as Gate 2 when Gate 1 (count check) fails but tests exist.
        On LLM failure returns (True, "") to avoid over-generating.
        """
        if not existing_e2e_tests:
            return False, "No existing E2E tests."
        rag = self.rag_service.rag
        if not rag or not rag.llm:
            return True, ""  # no LLM: assume sufficient
        if _llm_delay_sec() > 0:
            time.sleep(_llm_delay_sec())

        req_summaries = []
        for r in requirements[:10]:
            req_id = r.get("id", "")
            title = (r.get("title") or "")[:120]
            req_summaries.append(f"[{req_id}] {title}")

        e2e_summaries = []
        for t in existing_e2e_tests[:15]:
            tid = t.get("testrail_id") or "N/A"
            title = (t.get("title") or "")[:150]
            e2e_summaries.append(f"[{tid}] {title}")

        from langchain_core.prompts import ChatPromptTemplate
        prompt = ChatPromptTemplate.from_messages([
            ("system", """You are a test architect. Given a set of requirements and a list of EXISTING E2E test cases, decide if the existing E2E tests collectively provide sufficient cross-requirement workflow coverage.

Sufficient means: the existing E2E tests cover the key end-to-end user journeys that integrate these requirements with each other and with the existing product. There is no need to generate new E2E tests.

Insufficient means: important cross-requirement flows, integration scenarios, or user journeys are clearly missing from the existing E2E tests.

Return ONLY valid JSON (no markdown): {{"sufficient": true or false, "reason": "one short sentence"}}"""),
            ("human", "Requirements:\n{requirements}\n\nExisting E2E tests:\n{e2e_tests}\n\nJSON:"),
        ])
        try:
            chain = prompt | rag.llm
            result = chain.invoke({
                "requirements": "\n".join(req_summaries),
                "e2e_tests": "\n".join(e2e_summaries),
            })
            c = record_from_langchain_result("requirement_analysis.e2e_coverage_sufficient", result, run_id=run_id)
            if run_cost is not None and c is not None:
                run_cost[0] += c
            raw = result.content if hasattr(result, "content") and result.content is not None else str(result)
            if not isinstance(raw, str):
                raw = str(raw) if raw is not None else ""
            match = re.search(r"\{[\s\S]*\}", raw)
            if match:
                data = json.loads(match.group())
                sufficient = bool(data.get("sufficient", True))
                reason = (data.get("reason") or "").strip()
                return sufficient, reason
        except Exception as e:
            print(f"⚠️  _is_e2e_coverage_sufficient failed: {e}")
        return True, ""  # on parse/LLM failure, assume sufficient to avoid over-generating

    def _fetch_critical_product_tests(self, k_per_query: int = 8) -> List[Dict[str, Any]]:
        """
        Fetch a broad cross-section of critical product tests from ChromaDB using
        diverse semantic queries — not tied to any specific requirement.
        Returns up to 50 deduped test summaries for use as regression context.
        """
        rag = self.rag_service.rag
        if not rag or not rag.vectorstore:
            return []

        # Broad queries covering major product areas
        queries = [
            "account creation onboarding KYB KYC approval",
            "payment transfer send receive funds",
            "login authentication session security",
            "dashboard balance transactions history",
            "bill payment counterparty beneficiary",
            "settings profile management freeze closure",
        ]

        seen_ids: set = set()
        results: List[Dict[str, Any]] = []

        for query in queries:
            try:
                docs = rag.vectorstore.similarity_search_with_score(
                    query,
                    k=k_per_query,
                    filter={"source_type": "testcase"},
                )
                for doc, score in docs:
                    tid = doc.metadata.get("testrail_id") or doc.metadata.get("id") or ""
                    if tid and tid in seen_ids:
                        continue
                    if tid:
                        seen_ids.add(tid)
                    title = doc.metadata.get("title", "")[:120]
                    priority = doc.metadata.get("priority", "")
                    results.append({
                        "testrail_id": tid,
                        "title": title,
                        "priority": priority,
                        "similarity": round(float(score), 3),
                    })
                    if len(results) >= 50:
                        return results
            except Exception as e:
                print(f"[E2E] _fetch_critical_product_tests query failed: {e}")
                continue

        # Sort: P0 first, then P1, then by similarity desc
        _priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
        results.sort(key=lambda t: (_priority_order.get(t.get("priority", ""), 9), -t.get("similarity", 0)))
        return results[:50]

    def _identify_e2e_workflows(
        self,
        requirements: List[Dict[str, Any]],
        existing_tests: Dict[str, List[Dict]],
        existing_e2e_tests: Optional[List[Dict]] = None,
        critical_product_tests: Optional[List[Dict]] = None,
        run_id: Optional[str] = None,
        run_cost: Optional[List[float]] = None,
    ) -> List[Dict[str, Any]]:
        """
        LLM: Analyze new requirements + broad critical product tests to identify E2E regression workflows.
        Uses impact analysis to surface existing flows that could be affected by the new requirements.
        Returns list of workflows with: name, type, impacted_area, steps, expected_outcome, covers_requirements.
        """
        if not requirements:
            return []

        rag = self.rag_service.rag
        if not rag or not rag.llm:
            return []

        if _llm_delay_sec() > 0:
            time.sleep(_llm_delay_sec())

        # Build new requirements summary
        req_summaries = []
        for r in requirements[:15]:
            req_id = r.get("id", "")
            title = r.get("title", "")[:150]
            desc = r.get("description", "")[:250]
            req_summaries.append(f"[{req_id}] {title}\n{desc}")

        # Build critical product context — prefer broad product tests if available,
        # fall back to similarity-retrieved tests from related_tests
        critical_tests = critical_product_tests or []
        existing_features_for_prompt: List[str] = []
        seen_titles: set = set()

        # First: add broad critical product tests (cross-product areas)
        for t in critical_tests[:40]:
            title = (t.get("title") or "")[:100]
            if title and title not in seen_titles:
                seen_titles.add(title)
                tid = t.get("testrail_id", "")
                pri = t.get("priority", "")
                existing_features_for_prompt.append(f"[{tid}][{pri}] {title}")

        # Then: add similarity-retrieved tests (new feature area context)
        for req_id, tests in existing_tests.items():
            for t in tests[:3]:
                title = (t.get("title") or "")[:100]
                if title and title not in seen_titles:
                    seen_titles.add(title)
                    tid = t.get("testrail_id", "")
                    existing_features_for_prompt.append(f"[{tid}] {title}")

        from langchain_core.prompts import ChatPromptTemplate
        prompt = ChatPromptTemplate.from_messages([
            ("system", """You are a senior QA architect at {company_name}. Your job is to design TRUE END-TO-END REGRESSION test workflows.

EXISTING PRODUCT TEST COVERAGE (broad cross-product sample):
{existing_features}

EXISTING E2E/REGRESSION TESTS (do NOT duplicate these):
{existing_e2e_tests}

---
TASK: Design E2E regression workflows for the {n_requirements} new requirements below.

Think in two passes:
1. IMPACT ANALYSIS — identify all existing product areas that could be AFFECTED by these requirements (e.g., new AUD account feature may affect: FX balance display, outbound payment routing, counterparty creation, statement generation)
2. WORKFLOW DESIGN — generate one workflow per coverage target:
   a) One "new_feature" workflow per new requirement (covering the new functionality)
   b) One "regression" workflow per impacted area you identify (covering existing flows that could break)
   c) One "integration" workflow where a new feature and an existing area must work together end-to-end
   Target count = {n_requirements} new requirements + N impacted areas (do not arbitrarily limit — cover every target)

Each workflow must:
- Be a real user journey from login/entry-point to a meaningful outcome
- Cover either a NEW flow the requirements introduce OR an EXISTING flow that the new feature could BREAK or CHANGE
- NOT already be covered by the existing E2E tests listed above

Return ONLY valid JSON (no markdown):
{{
  "impacted_areas": ["area1", "area2"],
  "workflows": [
    {{
      "name": "Short workflow name",
      "type": "new_feature | regression | integration",
      "impacted_area": "Which existing product area this covers (if regression/integration)",
      "description": "Complete user journey from entry point to outcome",
      "entry_point": "Starting state (e.g., 'User logged in with AUD-enabled account')",
      "steps": ["Step 1: ...", "Step 2: ...", "Step 3: ..."],
      "expected_outcome": "What the user sees/achieves at the end",
      "covers_requirements": ["REQ-1", "REQ-2"],
      "rationale": "Why this is important for regression"
    }}
  ]
}}

If no meaningful E2E workflows can be identified, return {{"impacted_areas": [], "workflows": []}}"""),
            ("human", "NEW REQUIREMENTS:\n{requirements}\n\nJSON:"),
        ])

        existing_features_text = (
            "\n".join(existing_features_for_prompt[:45])
            if existing_features_for_prompt
            else "No existing tests found — assume standard product features: login, dashboard, accounts, payments, transactions"
        )
        existing_e2e_text = "\n".join(
            f"[{t.get('testrail_id', '')}] {t.get('title', '')}"
            for t in (existing_e2e_tests or [])[:20]
        ) or "None"

        try:
            chain = prompt | rag.llm
            result = chain.invoke({
                "company_name": _get_company_name(),
                "existing_features": existing_features_text,
                "existing_e2e_tests": existing_e2e_text,
                "requirements": "\n---\n".join(req_summaries),
                "n_requirements": len(req_summaries),
            })
            c = record_from_langchain_result("requirement_analysis.identify_e2e_workflows", result, run_id=run_id)
            if run_cost is not None and c is not None:
                run_cost[0] += c
            raw = result.content if hasattr(result, "content") and result.content is not None else str(result)
            if not isinstance(raw, str):
                raw = str(raw) if raw is not None else ""
            print(f"📊 E2E Workflows LLM response: {raw[:500]}...")
            match = re.search(r"\{[\s\S]*\}", raw)
            if match:
                data = json.loads(match.group())
                impacted = data.get("impacted_areas", [])
                if impacted:
                    print(f"[E2E] Impacted areas identified: {impacted}")
                workflows = data.get("workflows", [])
                # Cap at requirements × 4 to prevent runaway output, but never less than 20
                _cap = max(20, len(req_summaries) * 4)
                return workflows[:_cap]
        except Exception as e:
            print(f"⚠️  _identify_e2e_workflows failed: {e}")
        return []

    def _generate_e2e_test(
        self,
        workflow: Dict[str, Any],
        requirements: List[Dict[str, Any]],
        run_id: Optional[str] = None,
        run_cost: Optional[List[float]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        LLM: Generate a single E2E test case for a workflow that integrates new requirements
        with existing product features.
        Returns test case dict with title, preconditions, steps, expected_result.
        """
        rag = self.rag_service.rag
        if not rag or not rag.llm:
            return None
        
        if _llm_delay_sec() > 0:
            time.sleep(_llm_delay_sec())

        # Build requirements context for this workflow
        req_map = {r.get("id"): r for r in requirements}
        workflow_reqs = []
        for rid in workflow.get("new_requirement_ids", workflow.get("requirement_ids", [])):
            r = req_map.get(rid)
            if r:
                workflow_reqs.append({
                    "id": rid,
                    "title": r.get("title", "")[:200],
                    "description": r.get("description", "")[:400],
                })

        from langchain_core.prompts import ChatPromptTemplate
        prompt = ChatPromptTemplate.from_messages([
            ("system", """You are a test engineer at {company_name} creating an END-TO-END test case that covers a complete user journey integrating new features with existing product functionality.

Given a workflow definition, create ONE comprehensive E2E test case that:
1. Starts from the existing entry point (e.g., user logged in)
2. Navigates through existing features as needed
3. Executes the new functionality
4. Verifies the outcome integrates correctly with the product

Return ONLY valid JSON (no markdown):
{{
  "title": "<Descriptive test title>",
  "priority": "P0",
  "preconditions": "1. User has valid account\\n2. User is logged in\\n3. Any other required state",
  "steps": "1. User navigates to X\\n2. User clicks on Y\\n3. User enters Z\\n4. User verifies outcome",
  "expected_result": "Final expected outcome describing what the user should see and verify",
  "covers_requirements": ["REQ-1", "REQ-2"]
}}

Important:
- Title should be descriptive of the business flow. Do NOT include priority labels like (P0) or (P1) in the title.
- Steps should be numbered, detailed, and written from user perspective
- Include realistic test data examples where relevant
- Expected result should be specific and verifiable"""),
            ("human", """Workflow: {workflow_name}
Description: {workflow_desc}
Entry Point: {entry_point}
Existing Features Used: {existing_features}
Workflow Steps Outline: {workflow_steps}
Expected Outcome: {expected_outcome}

New Requirements Being Tested:
{requirements}

JSON:"""),
        ])

        try:
            req_text = "\n---\n".join([
                f"[{r['id']}] {r['title']}\n{r['description']}"
                for r in workflow_reqs
            ]) if workflow_reqs else "See workflow steps above"
            
            chain = prompt | rag.llm
            result = chain.invoke({
                "company_name": _get_company_name(),
                "workflow_name": workflow.get("name", "E2E Workflow"),
                "workflow_desc": workflow.get("description", ""),
                "entry_point": workflow.get("existing_entry_point", "User logged into the application dashboard"),
                "existing_features": ", ".join(workflow.get("existing_features_used", [])) or "Standard navigation",
                "workflow_steps": "\n".join(workflow.get("workflow_steps", [])) or "See description",
                "expected_outcome": workflow.get("expected_outcome", "Feature works as expected"),
                "requirements": req_text,
            })
            c = record_from_langchain_result("requirement_analysis.generate_e2e_test", result, run_id=run_id)
            if run_cost is not None and c is not None:
                run_cost[0] += c
            raw = result.content if hasattr(result, "content") and result.content is not None else str(result)
            if not isinstance(raw, str):
                raw = str(raw) if raw is not None else ""
            match = re.search(r"\{[\s\S]*\}", raw)
            if match:
                test_data = json.loads(match.group())
                # Strip priority suffix from title if LLM added it (e.g. "Title (P0)")
                if test_data.get("title"):
                    test_data["title"] = re.sub(r'\s*\((P[0-3]|Regression|Integration|New[_ ]?Feature)\)\s*$', '', test_data["title"], flags=re.IGNORECASE).strip()
                # Add workflow metadata
                test_data["workflow_name"] = workflow.get("name", "")
                test_data["workflow_description"] = workflow.get("description", "")
                test_data["existing_entry_point"] = workflow.get("existing_entry_point", "")
                test_data["existing_features_used"] = workflow.get("existing_features_used", [])
                test_data["requirement_ids"] = workflow.get("new_requirement_ids", workflow.get("requirement_ids", []))
                test_data["case_type"] = "FCT / Regression"
                return test_data
        except Exception as e:
            print(f"⚠️  _generate_e2e_test failed: {e}")
        return None

    def _generate_e2e_tests_batch(
        self,
        workflows: List[Dict[str, Any]],
        requirements: List[Dict[str, Any]],
        run_id: Optional[str] = None,
        run_cost: Optional[List[float]] = None,
    ) -> List[Dict[str, Any]]:
        """
        LLM: Generate ALL E2E test cases in a single batch call.
        One JSON array response instead of N sequential calls.
        Returns list of test case dicts (same structure as _generate_e2e_test).
        """
        rag = self.rag_service.rag
        if not rag or not rag.llm:
            return []
        if not workflows:
            return []

        if _llm_delay_sec() > 0:
            time.sleep(_llm_delay_sec())

        req_map = {r.get("id"): r for r in requirements}

        wf_blocks = []
        for i, wf in enumerate(workflows):
            # Support both new schema (covers_requirements, steps, entry_point)
            # and old schema (new_requirement_ids, workflow_steps, existing_entry_point)
            req_ids = wf.get("covers_requirements") or wf.get("new_requirement_ids") or wf.get("requirement_ids") or []
            wf_reqs = []
            for rid in req_ids:
                r = req_map.get(rid)
                if r:
                    wf_reqs.append(f"[{rid}] {r.get('title', '')[:150]}: {r.get('description', '')[:300]}")
            steps_list = wf.get("steps") or wf.get("workflow_steps") or []
            entry_point = wf.get("entry_point") or wf.get("existing_entry_point") or "User logged into the application dashboard"
            wf_type = wf.get("type", "")
            impacted = wf.get("impacted_area") or ", ".join(wf.get("existing_features_used") or []) or "Standard navigation"
            block = (
                f"Workflow {i + 1}: {wf.get('name', 'E2E Workflow')}\n"
                f"Type: {wf_type or 'e2e'}\n"
                f"Description: {wf.get('description', '')}\n"
                f"Entry Point: {entry_point}\n"
                f"Impacted/Existing Area: {impacted}\n"
                f"Steps: {chr(10).join(steps_list) or 'See description'}\n"
                f"Expected Outcome: {wf.get('expected_outcome', 'Feature works as expected')}\n"
                f"Rationale: {wf.get('rationale', '')}\n"
                f"Requirements: {chr(10).join(wf_reqs) or 'See workflow steps'}"
            )
            wf_blocks.append(block)

        workflows_text = "\n\n---\n\n".join(wf_blocks)

        from langchain_core.prompts import ChatPromptTemplate
        prompt = ChatPromptTemplate.from_messages([
            ("system", """You are a test engineer at {company_name} creating END-TO-END test cases that cover complete user journeys integrating new features with existing product functionality.

For EACH workflow provided, create ONE comprehensive E2E test case.

Return ONLY a valid JSON array (no markdown), one object per workflow, in the SAME ORDER as the input:
[
  {{
    "title": "<Descriptive test title>",
    "priority": "P0",
    "preconditions": "1. User has valid account\\n2. User is logged in\\n3. Any other required state",
    "steps": "1. Navigate to X\\n2. Click on Y\\n3. Enter Z\\n4. Verify outcome",
    "expected_result": "Final expected outcome describing what the user should see and verify",
    "covers_requirements": ["REQ-1", "REQ-2"]
  }}
]

Rules:
- One object per workflow, same order as input
- Title: descriptive test case title, no suffixes like (P0), (Regression), (Integration), or (New Feature)
- Steps: numbered, detailed, written from user perspective
- Include realistic test data examples where relevant"""),
            ("human", """{workflows}

JSON array ({count} test cases):"""),
        ])

        try:
            chain = prompt | rag.llm
            result = chain.invoke({
                "company_name": _get_company_name(),
                "workflows": workflows_text,
                "count": len(workflows),
            })
            c = record_from_langchain_result("requirement_analysis.generate_e2e_tests_batch", result, run_id=run_id)
            if run_cost is not None and c is not None:
                run_cost[0] += c
            raw = result.content if hasattr(result, "content") and result.content is not None else str(result)
            if not isinstance(raw, str):
                raw = str(raw) if raw is not None else ""

            match = re.search(r"\[[\s\S]*\]", raw)
            if not match:
                print(f"⚠️  _generate_e2e_tests_batch: no JSON array in response, falling back to sequential")
                return self._generate_e2e_tests_sequential_fallback(workflows, requirements, run_id=run_id, run_cost=run_cost)

            tests_data = json.loads(match.group())
            results = []
            for i, test_data in enumerate(tests_data):
                if i >= len(workflows):
                    break
                wf = workflows[i]
                if test_data.get("title"):
                    test_data["title"] = re.sub(r'\s*\((P[0-3]|Regression|Integration|New[_ ]?Feature)\)\s*$', '', test_data["title"], flags=re.IGNORECASE).strip()
                test_data["workflow_name"] = wf.get("name", "")
                test_data["workflow_description"] = wf.get("description", "")
                test_data["existing_entry_point"] = wf.get("existing_entry_point", "")
                test_data["existing_features_used"] = wf.get("existing_features_used", [])
                test_data["requirement_ids"] = wf.get("new_requirement_ids", wf.get("requirement_ids", []))
                test_data["case_type"] = "FCT / Regression"
                results.append(test_data)
            print(f"[E2E BATCH] Generated {len(results)}/{len(workflows)} E2E tests in one call")
            return results
        except Exception as e:
            print(f"⚠️  _generate_e2e_tests_batch failed: {e}, falling back to sequential")
            return self._generate_e2e_tests_sequential_fallback(workflows, requirements, run_id=run_id, run_cost=run_cost)

    def _generate_e2e_tests_sequential_fallback(
        self,
        workflows: List[Dict[str, Any]],
        requirements: List[Dict[str, Any]],
        run_id: Optional[str] = None,
        run_cost: Optional[List[float]] = None,
    ) -> List[Dict[str, Any]]:
        """Fallback: call _generate_e2e_test sequentially if batch fails."""
        results = []
        for wf in workflows:
            t = self._generate_e2e_test(wf, requirements, run_id=run_id, run_cost=run_cost)
            if t:
                results.append(t)
        return results

    def _resolve_sections_from_related(
        self,
        related_tests: Dict[str, List[Dict]],
        default_section_id: int,
    ) -> Dict[str, int]:
        """Resolve section_id per requirement from first related test's case (get_case). Fallback to default_section_id."""
        from backend.rag.rag_settings import get_config
        config = get_config()
        url = getattr(config, "testrail_url", None) or os.getenv("TESTRAIL_URL", "")
        email = getattr(config, "testrail_email", None) or os.getenv("TESTRAIL_EMAIL", "")
        api_key = getattr(config, "testrail_api_key", None) or os.getenv("TESTRAIL_API_KEY", "")
        if not url or not email or not api_key:
            return {}
        from backend.connectors.testrail_connector import TestRailConnector
        connector = TestRailConnector(url=url, email=email, api_key=api_key)
        section_map: Dict[str, int] = {}
        for req_id, tests in related_tests.items():
            if not tests:
                section_map[req_id] = default_section_id
                continue
            tid = (tests[0].get("testrail_id") or "").strip().upper()
            if tid.startswith("C"):
                try:
                    case_id = int(tid[1:].strip())
                    case = connector.get_case(case_id)
                    sid = case.get("section_id")
                    if sid:
                        section_map[req_id] = int(sid)
                    else:
                        section_map[req_id] = default_section_id
                except Exception:
                    section_map[req_id] = default_section_id
            else:
                section_map[req_id] = default_section_id
        return section_map

    def _push_generated_tests_to_testrail(
        self,
        generated_tests: Dict[str, List[Dict]],
        section_id_or_map: Any,
    ) -> List[Dict]:
        """Push generated tests to TestRail via add_case. section_id_or_map: int (same section for all) or Dict[str,int] (req_id -> section_id). Returns list of {requirement_id, testrail_id, success, error?}."""
        from backend.rag.rag_settings import get_config
        config = get_config()
        url = getattr(config, "testrail_url", None) or os.getenv("TESTRAIL_URL", "")
        email = getattr(config, "testrail_email", None) or os.getenv("TESTRAIL_EMAIL", "")
        api_key = getattr(config, "testrail_api_key", None) or os.getenv("TESTRAIL_API_KEY", "")
        if not url or not email or not api_key:
            return []
        from backend.connectors.testrail_connector import TestRailConnector
        connector = TestRailConnector(url=url, email=email, api_key=api_key)
        priority_map = {"P0": 4, "P1": 3, "P2": 2, "P3": 1}
        use_map = isinstance(section_id_or_map, dict)
        results = []
        any_ingested = False
        for req_id, tests in generated_tests.items():
            section_id = section_id_or_map.get(req_id, 0) if use_map else section_id_or_map
            if not section_id:
                for gt in tests:
                    results.append({"requirement_id": req_id, "testrail_id": None, "success": False, "error": "No section for this requirement (use_section_of_related had no related test and no default)"})
                continue
            for gt in tests:
                title = gt.get("title") or f"Generated for {req_id}"
                steps = gt.get("steps") or ""
                expected = gt.get("expected_result") or ""
                preconds = gt.get("preconditions") or ""
                prio = (gt.get("priority") or "P2").upper().strip()
                prio_id = priority_map.get(prio, 2)
                case_type_name = gt.get("case_type") or ""
                type_id = None
                if case_type_name:
                    try:
                        ct_map = connector._build_case_type_map()
                        type_id = next((tid for tid, name in ct_map.items() if name.lower() == case_type_name.lower()), None)
                    except Exception:
                        pass
                try:
                    case = connector.add_case(
                        section_id=section_id,
                        title=title,
                        steps=steps,
                        expected_result=expected,
                        preconditions=preconds,
                        priority_id=prio_id,
                        platform="web / m-web",
                        type_id=type_id,
                    )
                    testrail_id = f"C{case.get('id', '')}"
                    results.append({
                        "requirement_id": req_id,
                        "testrail_id": testrail_id,
                        "success": True,
                    })
                    # Ingest into ChromaDB so the next requirement analysis finds these tests (avoids re-generating the same tests)
                    try:
                        if self.ingest_pushed_case_into_rag(
                            testrail_id=testrail_id,
                            title=title,
                            steps=steps,
                            expected_result=expected,
                            preconditions=preconds,
                            priority=prio,
                            requirement_text=None,
                            case_type=case_type_name or "Functional",
                        ):
                            any_ingested = True
                    except Exception as ing_err:
                        print(f"Requirement analysis: ingest after push failed for {testrail_id}: {ing_err}")
                except Exception as e:
                    results.append({
                        "requirement_id": req_id,
                        "testrail_id": None,
                        "success": False,
                        "error": str(e),
                    })
        if any_ingested and self.rag_service and hasattr(self.rag_service, "invalidate_vectorstore_for_reload"):
            self.rag_service.invalidate_vectorstore_for_reload()
        return results

    def push_tests_to_testrail(
        self,
        generated_tests: Dict[str, List[Dict]],
        related_tests: Optional[Dict[str, List[Dict]]] = None,
        use_section_of_related: bool = False,
        target_section_id: Optional[int] = None,
    ) -> List[Dict]:
        """
        Push a subset of generated tests to TestRail (e.g. user-selected from UI).
        Section resolution: use_section_of_related + related_tests, else target_section_id.
        """
        if not generated_tests:
            return []
        default_section = target_section_id or 0
        push_enabled = False
        try:
            from backend.rag.rag_settings import get_config
            config = get_config()
            push_enabled = getattr(config, "testrail_push_enabled", False) or os.getenv("TESTRAIL_PUSH_ENABLED", "").lower() == "true"
        except Exception:
            pass
        if not push_enabled:
            return []
        if use_section_of_related and related_tests:
            section_map = self._resolve_sections_from_related(related_tests, default_section)
            for req_id in generated_tests:
                section_map.setdefault(req_id, default_section)
            return self._push_generated_tests_to_testrail(generated_tests, section_map)
        if default_section:
            return self._push_generated_tests_to_testrail(generated_tests, default_section)
        return []

    def _generate_tests_for_requirement(
        self,
        requirement: Dict[str, Any],
        context_tests: List[Dict],
        specs_context: Optional[List[Dict]] = None,
        reuse_test_ids: Optional[List[str]] = None,
        update_test_infos: Optional[List[Dict]] = None,
        coverage_gap_reason: str = "",
        generate_p2_p3: bool = False,
        allowed_priorities: Optional[List[str]] = None,
        acceptance_criteria: Optional[List[str]] = None,
        run_id: Optional[str] = None,
        run_cost: Optional[List[float]] = None,
    ) -> List[Dict[str, Any]]:
        """Generate additional E2E test cases for an uncovered requirement (reuse first, create only gaps).
        Uses prior Confluence/specs + related TestRail tests. reuse_test_ids/update_test_infos are already
        in the E2E set; generate ONLY tests that fill remaining gaps. allowed_priorities restricts which
        priorities to generate (e.g. only P1 when we already have >= 3 P0). If None, uses P0/P1 or P0-P3 per generate_p2_p3."""
        rag = self.rag_service.rag
        if not rag or not rag.llm:
            return []
        if _llm_delay_sec() > 0:
            time.sleep(_llm_delay_sec())

        req_id = requirement.get("id", "REQ-1")
        req_title = (requirement.get("title") or "").strip()
        req_desc = (requirement.get("description") or "").strip()
        _body = (f"{req_id}: " if req_id else "") + req_title + ("\n" + req_desc if req_desc else "")
        canonical_req = ("Requirement: " + _body.strip()) if _body.strip() else (req_title + "\n" + req_desc).strip()
        context_parts = [canonical_req]

        # Already in E2E set: reuse as-is and use after update. Do NOT duplicate these.
        reuse_ids = reuse_test_ids or []
        update_infos = update_test_infos or []
        if reuse_ids or update_infos:
            context_parts.append("\nAlready in E2E set (do NOT duplicate):")
            if reuse_ids:
                context_parts.append("  Reuse as-is: " + ", ".join(str(x) for x in reuse_ids))
            if update_infos:
                for u in update_infos[:10]:
                    tid = u.get("testrail_id") or "?"
                    title = (u.get("title") or "")[:80]
                    context_parts.append(f"  Use after update: [{tid}] {title}")
            context_parts.append("Generate ONLY additional test cases needed to cover any remaining acceptance criteria or flows not covered by the above.")
        if coverage_gap_reason and coverage_gap_reason.strip():
            context_parts.append("\nExisting tests miss: " + coverage_gap_reason.strip())
            context_parts.append("Generate only tests that address this gap.")

        if specs_context:
            context_parts.append("\nConfluence feature documentation (any documented behavior not yet tested is a gap you must fill):\n")
            for s in specs_context[:4]:
                title = s.get("title", "")
                content = (s.get("content") or "")[:500]
                if content:
                    context_parts.append(f"[{title}]\n{content}\n")

        # Acceptance criteria: explicit list of what each test must cover
        if acceptance_criteria:
            ac_lines = "\n".join(f"  {i+1}. {ac}" for i, ac in enumerate(acceptance_criteria))
            context_parts.append(
                f"\nAcceptance Criteria (EVERY AC must be covered by at least one generated test):\n{ac_lines}\n"
                "Map each new test to a specific AC above. Do not generate multiple tests for the same AC unless priorities differ."
            )

        if context_tests:
            context_parts.append("\nExample existing tests from TestRail (follow this structure):\n")
            for t in context_tests[:3]:
                context_parts.append(t.get("content", "")[:500])

        # Restrict to allowed priorities (e.g. only P1 when we already have >= 3 P0)
        if allowed_priorities and len(allowed_priorities) > 0:
            priorities_set = [p.strip().upper() for p in allowed_priorities if (p or "").strip().upper() in ("P0", "P1", "P2", "P3")]
            if not priorities_set:
                priorities_set = ["P0", "P1"] if not generate_p2_p3 else ["P0", "P1", "P2", "P3"]
        else:
            priorities_set = ["P0", "P1"] if not generate_p2_p3 else ["P0", "P1", "P2", "P3"]
        priority_rule = "Generate ONLY " + ", ".join(priorities_set) + " tests. No other priorities."
        priority_restriction = "Generate ONLY " + ", ".join(priorities_set) + " tests. Do not generate P0, P1, P2, or P3 that are not in this list."
        allowed_priorities_str = ", ".join(f'"{p}"' for p in priorities_set)

        from langchain_core.prompts import ChatPromptTemplate

        # Dynamic per-priority guidance based on what priorities are requested
        # Scale target to acceptance criteria count: complex requirements need more tests
        _n_acs = len(acceptance_criteria) if acceptance_criteria else 1
        _per_priority_count = max(2, _n_acs)
        _priority_guidance_lines = []
        if "P0" in priorities_set:
            _priority_guidance_lines.append("P0 (Critical): happy path / core success flows")
        if "P1" in priorities_set:
            _priority_guidance_lines.append("P1 (High): key negative paths, validation failures, error states")
        if "P2" in priorities_set:
            _priority_guidance_lines.append("P2 (Medium): edge cases, boundary conditions, less common but valid flows")
        if "P3" in priorities_set:
            _priority_guidance_lines.append("P3 (Low): UI/display states, optional feature variations, non-critical paths")
        _priority_guidance = "\n".join(f"  - {l}" for l in _priority_guidance_lines)
        _total_target = min(12, len(priorities_set) * _per_priority_count)

        prompt = ChatPromptTemplate.from_messages([
            ("system", """You are a test analyst at {company_name}. Generate ADDITIONAL functional test cases to fill coverage gaps. Keep tests automation-friendly (clear steps, one scenario per test, stable flows). Use {company_name} product terminology in test steps and titles.

Rules:
1. PRIORITY: """ + priority_restriction + """
2. If the existing tests (Reuse as-is / Use after update) already fully cover the requirement, return an empty array [] — do not generate any new tests.
3. DO NOT DUPLICATE: Tests already listed as "Reuse as-is" or "Use after update" already exist. Generate ONLY new tests that cover acceptance criteria or flows NOT already covered by those.
4. FUNCTIONAL TESTS: Each test must cover a clear user scenario from start to outcome (e.g. login → navigate → action → verify). Automation-friendly: clear, repeatable steps and assertions. No unit-level or single-step tests.
5. PRIORITY COVERAGE: Generate at least 1 test for EACH priority in the allowed list. Priority meanings:
""" + _priority_guidance + """
6. PRODUCT CONTEXT: Use your organization's flows and terminology where applicable.
7. Structure: Each test must have Title, Priority (""" + allowed_priorities_str + """), Preconditions, Steps (numbered list, one step per line), Expected Result. Match the TestRail template.
8. TITLE: Do NOT include priority labels in the title (no "(P0)", "(P1)", etc.) — priority is a separate field.
9. Return a JSON array only (no markdown): [{{"title": "...", "priority": "P0", "preconditions": "...", "steps": "1. ...\\n2. ...", "expected_result": "..."}}, ...]
Do not fabricate product details; use only the requirement and prior specs."""),
            ("human", "Context:\n{context}\n\nGenerate ONLY additional functional test cases as a JSON array (do not duplicate existing tests). Use " + priority_rule + f" Return up to {_total_target} tests total (target ~{_per_priority_count} per priority level). Generate at least 1 test for each priority in: {', '.join(priorities_set)}. If existing tests already fully cover the requirement, return []. Order by priority: P0 first, then P1, then P2/P3. Automation-friendly. No unit-level tests."),
        ])
        try:
            chain = prompt | rag.llm
            result = chain.invoke({"context": "\n".join(context_parts), "company_name": _get_company_name()})
            c = record_from_langchain_result("requirement_analysis.generate_tests", result, extra={"requirement_id": req_id}, run_id=run_id)
            if run_cost is not None and c is not None:
                run_cost[0] += c
            content = result.content if hasattr(result, "content") else str(result)
            import json
            arr_match = re.search(r"\[[\s\S]*\]", content)
            if arr_match:
                data_list = json.loads(arr_match.group())
                if not isinstance(data_list, list):
                    data_list = [data_list]
            else:
                obj_match = re.search(r"\{[\s\S]*\}", content)
                if obj_match:
                    data_list = [json.loads(obj_match.group())]
                else:
                    return []
            out = []
            allowed_set = set(priorities_set)
            priority_order = {"P0": 4, "P1": 3, "P2": 2, "P3": 1}
            for data in data_list[:15]:
                if isinstance(data, dict) and data.get("title"):
                    p = (data.get("priority") or "P1").upper().strip()
                    if p not in allowed_set:
                        # Remap to first allowed priority if LLM returned a non-allowed one
                        p = priorities_set[0] if priorities_set else "P1"
                    data["priority"] = p
                    # Strip priority suffix from title if LLM added it (e.g. "Title (P0)")
                    data["title"] = re.sub(r'\s*\(P[0-3]\)\s*$', '', data["title"], flags=re.IGNORECASE).strip()
                    data["requirement_id"] = req_id
                    data["generated"] = True
                    out.append(data)
            out.sort(key=lambda t: priority_order.get((t.get("priority") or "P1").upper(), 1), reverse=True)
            return out
        except Exception as e:
            print(f"⚠️  LLM test generation failed for {req_id}: {e}")
        return []

    def suggest_case_update(
        self,
        testrail_id: str,
        requirement_text: str,
        suggested_changes: Optional[List[str]] = None,
        reason: Optional[str] = None,
        current_title: Optional[str] = None,
        current_content: Optional[str] = None,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Use LLM to suggest an updated test case based on requirement and current test.
        If suggested_changes/reason are provided, applies only those changes (minimal edit mode).
        If suggested_changes/reason are empty, intelligently analyzes the requirement and suggests updates.
        If current_content is not provided, fetches the case from TestRail by testrail_id (e.g. C129563).
        Returns (result_dict, error_message). On success: (dict with title, steps, etc., None). On failure: (None, "reason").
        """
        rag = self.rag_service.rag
        if not rag or not rag.llm:
            return None, "LLM is not configured. Check your environment (e.g. OPENAI_API_KEY or LLM config)."
        case_id = (testrail_id or "").strip().upper().replace("C", "")
        if not case_id or not case_id.isdigit():
            return None, "Invalid test case ID. Use a TestRail case ID like C129563."
        case_id_int = int(case_id)
        content = current_content
        if content is None or content == "":
            try:
                from backend.rag.rag_settings import get_config
                config = get_config()
                url = getattr(config, "testrail_url", None) or os.getenv("TESTRAIL_URL", "")
                email = getattr(config, "testrail_email", None) or os.getenv("TESTRAIL_EMAIL", "")
                api_key = getattr(config, "testrail_api_key", None) or os.getenv("TESTRAIL_API_KEY", "")
                if url and email and api_key:
                    from backend.connectors.testrail_connector import TestRailConnector
                    connector = TestRailConnector(url=url, email=email, api_key=api_key)
                    case = connector.get_case(case_id_int)
                    title_tr = case.get("title") or ""
                    pre = self._clean_case_field(case.get("custom_preconds"))
                    steps_tr = self._clean_case_field(case.get("custom_steps"))
                    if isinstance(case.get("custom_steps_separated"), list):
                        steps_tr = "\n".join(
                            f"{i+1}. {self._clean_case_field(step.get('content', ''))}"
                            for i, step in enumerate(case["custom_steps_separated"])
                        )
                    exp = self._clean_case_field(case.get("custom_expected"))
                    content = f"Title: {title_tr}\nPreconditions: {pre}\nSteps: {steps_tr}\nExpected: {exp}"
                    if not current_title:
                        current_title = title_tr
            except Exception as e:
                print(f"⚠️  get_case failed for {testrail_id}: {e}")
                return None, f"Could not load test case from TestRail: {e!s}. Check TESTRAIL_URL, credentials, and case ID."
        else:
            content = content or (f"Title: {current_title or 'N/A'}" if current_title else "No content")
        
        from langchain_core.prompts import ChatPromptTemplate
        
        # Determine if we have explicit suggestions or need AI to analyze
        has_explicit_suggestions = bool(suggested_changes and any(s.strip() for s in suggested_changes)) or bool(reason and reason.strip())
        
        if has_explicit_suggestions:
            # Mode 1: Minimal edit mode - only apply explicit suggested changes
            prompt = ChatPromptTemplate.from_messages([
                ("system", """You are a test analyst at {company_name}. Your task is to suggest an UPDATED version of an existing test case. The user chose "Update with AI" because they want to KEEP the current test and only fix or tweak what is wrong. Use {company_name} product terminology.

RULES (strict):
1. Your output MUST be the current test with only minimal, targeted edits. Copy the current title, steps, preconditions, and expected_result almost verbatim.
2. Change ONLY what the "suggested_changes" or "reason" explicitly ask for. If something is not mentioned, leave it exactly as in the current test.
3. Do NOT rewrite, rephrase, or add new steps unless suggested_changes ask for it. Do NOT produce a different test that merely matches the requirement—that would be a new test, not an update.
4. Keep the same structure: same number of steps unless a change requires adding/removing one; same tone and wording elsewhere.
5. Preserve the test style: if the current test uses UI steps or user-facing expected results, keep them.

Return ONLY valid JSON (no markdown): {{"title": "...", "priority": "P0"|"P1"|"P2"|"P3", "preconditions": "...", "steps": "1. ...\\n2. ...", "expected_result": "..."}}
Priority exactly one of P0, P1, P2, P3. Steps as a numbered list with newlines (\\n)."""),
                ("human", "Requirement:\n{requirement}\n\nCurrent test case:\n{current_test}\n\nSuggested changes:\n{suggestions}\n\nReason: {reason}\n\nReturn the updated test case as JSON."),
            ])
            invoke_params = {
                "requirement": (requirement_text or "")[:2500],
                "current_test": (content or "")[:6000],
                "suggestions": "\n".join(suggested_changes[:15]) if suggested_changes else "None",
                "reason": (reason or "")[:500],
            }
        else:
            # Mode 2: Smart update mode - AI analyzes requirement and suggests improvements
            prompt = ChatPromptTemplate.from_messages([
                ("system", """You are a test analyst at {company_name}. Your task is to analyze a requirement and an existing related test case, then suggest updates to make the test case better aligned with the requirement. Use {company_name} product terminology.

ANALYSIS APPROACH:
1. Compare the requirement with the current test case
2. Identify gaps: what aspects of the requirement are NOT covered by the test?
3. Identify outdated parts: do any steps or expected results reference old behavior?
4. Identify missing validations: should additional verifications be added?

UPDATE RULES:
1. Keep the core structure and intent of the original test
2. Add/modify steps to cover requirement aspects not currently tested
3. Update expected results to match current requirement behavior
4. Update preconditions if the requirement specifies new prerequisites
5. Keep the same testing style (UI vs API, manual vs automated wording)
6. If the test is already well-aligned with the requirement, make minimal improvements

Return ONLY valid JSON (no markdown): {{"title": "...", "priority": "P0"|"P1"|"P2"|"P3", "preconditions": "...", "steps": "1. ...\\n2. ...", "expected_result": "..."}}
Priority exactly one of P0, P1, P2, P3. Steps as a numbered list with newlines (\\n)."""),
                ("human", "Requirement:\n{requirement}\n\nCurrent test case:\n{current_test}\n\nAnalyze the requirement and suggest an updated version of the test case that better covers the requirement. Return the updated test case as JSON."),
            ])
            invoke_params = {
                "requirement": (requirement_text or "")[:2500],
                "current_test": (content or "")[:6000],
            }
        
        invoke_params["company_name"] = _get_company_name()
        try:
            chain = prompt | rag.llm
            result = chain.invoke(invoke_params)
            raw = getattr(result, "content", None)
            if not isinstance(raw, str):
                raw = str(raw) if raw is not None else ""
            match = re.search(r"\{[\s\S]*\}", raw)
            if match:
                data = json.loads(match.group())
                if isinstance(data, dict) and data.get("title"):
                    return data, None
            return None, "The model did not return a valid test case (missing title or invalid JSON). Try again."
        except json.JSONDecodeError as e:
            print(f"⚠️  suggest_case_update JSON parse failed for {testrail_id}: {e}")
            return None, "Invalid JSON in model response. Try again."
        except Exception as e:
            print(f"⚠️  suggest_case_update failed for {testrail_id}: {e}")
            return None, str(e) or "Could not generate suggestion. Try again."

    def improve_for_automation(
        self,
        testrail_id: str,
        title: str,
        preconditions: str = '',
        steps: str = '',
        expected_result: str = '',
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Rewrite a TestRail test case to be clearer and more actionable for UI/API automation.
        Returns (result_dict, error_message). result_dict has title, preconditions, steps, expected_result, priority.
        """
        rag = self.rag_service.rag
        if not rag or not rag.llm:
            return None, "LLM is not configured."

        current_content = (
            f"Title: {title}\n"
            f"Preconditions: {preconditions or 'N/A'}\n"
            f"Steps: {steps or 'N/A'}\n"
            f"Expected Result: {expected_result or 'N/A'}"
        )

        from langchain_core.prompts import ChatPromptTemplate
        prompt = ChatPromptTemplate.from_messages([
            ("system", """You are a senior QA automation engineer at {company_name}. Your job is to rewrite a manual test case so it is clear and unambiguous for an automation framework (Playwright/Selenium).

RULES:
1. Keep the same intent and scope — do NOT add new test scenarios or change what is being tested.
2. Make each step atomic and deterministic: replace vague steps like "fill in the form" with specific actions like "Enter 'user@example.com' in the Email field".
3. Use precise UI element identifiers where implied (button labels, field names, URLs).
4. Preconditions must list everything needed before step 1 (logged-in state, data setup, etc.).
5. Expected result must be a verifiable assertion, not a description.
6. Keep the priority the same unless the original has no priority — default to P2.
7. Return ONLY valid JSON (no markdown): {{"title": "...", "priority": "P0"|"P1"|"P2"|"P3", "preconditions": "...", "steps": "1. ...\\n2. ...", "expected_result": "..."}}"""),
            ("human", "Rewrite the following test case for automation clarity:\n\n{current_test}"),
        ])

        try:
            chain = prompt | rag.llm
            result = chain.invoke({
                "company_name": _get_company_name(),
                "current_test": current_content[:6000],
            })
            raw = getattr(result, "content", None)
            if not isinstance(raw, str):
                raw = str(raw) if raw is not None else ""
            match = re.search(r"\{[\s\S]*\}", raw)
            if match:
                data = json.loads(match.group())
                if isinstance(data, dict) and data.get("title"):
                    return data, None
            return None, "Model did not return valid JSON. Try again."
        except json.JSONDecodeError:
            return None, "Invalid JSON in model response. Try again."
        except Exception as e:
            return None, str(e) or "Could not generate suggestion. Try again."

    @staticmethod
    def _clean_case_field(html_text: Any) -> str:
        """Strip HTML from a case field for plain-text display."""
        if not html_text:
            return ""
        from backend.connectors.testrail_connector import TestRailConnector
        return TestRailConnector._clean_html(html_text)

    def update_case_in_testrail(
        self,
        testrail_id: str,
        title: str,
        steps: Optional[str] = None,
        expected_result: Optional[str] = None,
        preconditions: Optional[str] = None,
        priority: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Update an existing test case in TestRail. Returns { success, testrail_id, error? }.
        priority: P0/P1/P2/P3 mapped to TestRail priority_id.
        """
        case_id_str = (testrail_id or "").strip().upper().replace("C", "")
        if not case_id_str or not case_id_str.isdigit():
            return {"success": False, "testrail_id": testrail_id, "error": "Invalid testrail_id"}
        case_id = int(case_id_str)
        try:
            from backend.rag.rag_settings import get_config
            config = get_config()
            url = getattr(config, "testrail_url", None) or os.getenv("TESTRAIL_URL", "")
            email = getattr(config, "testrail_email", None) or os.getenv("TESTRAIL_EMAIL", "")
            api_key = getattr(config, "testrail_api_key", None) or os.getenv("TESTRAIL_API_KEY", "")
            if not url or not email or api_key is None:
                return {"success": False, "testrail_id": testrail_id, "error": "TestRail not configured"}
            push_enabled = getattr(config, "testrail_push_enabled", False) or os.getenv("TESTRAIL_PUSH_ENABLED", "").lower() == "true"
            if not push_enabled:
                return {"success": False, "testrail_id": testrail_id, "error": "TestRail push is disabled"}
            from backend.connectors.testrail_connector import TestRailConnector
            connector = TestRailConnector(url=url, email=email, api_key=api_key)
            priority_map = {"P0": 4, "P1": 3, "P2": 2, "P3": 1}
            priority_id = None
            if priority:
                priority_id = priority_map.get((priority or "").upper().strip())
            connector.update_case(
                case_id,
                title=title,
                steps=steps,
                expected_result=expected_result,
                preconditions=preconditions,
                priority_id=priority_id,
            )
            return {"success": True, "testrail_id": f"C{case_id}"}
        except Exception as e:
            return {"success": False, "testrail_id": testrail_id, "error": str(e)}

    def create_case_in_testrail(
        self,
        section_id: int,
        title: str,
        steps: Optional[str] = None,
        expected_result: Optional[str] = None,
        preconditions: Optional[str] = None,
        priority: Optional[str] = None,
        requirement_text: Optional[str] = None,
        platform: Optional[str] = None,
        case_type_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a new test case in TestRail in the given section.
        Returns { success, testrail_id, error? }.
        priority: P0/P1/P2/P3 mapped to TestRail priority_id.
        requirement_text: Optional; stored in TestRail custom field if project has a "requirement" field (survives sync).
        platform: Optional platform label (e.g. "Web", "API") so the Platform field is not blank in TestRail.
        """
        if not section_id or not title or not title.strip():
            return {"success": False, "testrail_id": None, "error": "section_id and title are required"}
        try:
            from backend.rag.rag_settings import get_config
            config = get_config()
            url = getattr(config, "testrail_url", None) or os.getenv("TESTRAIL_URL", "")
            email = getattr(config, "testrail_email", None) or os.getenv("TESTRAIL_EMAIL", "")
            api_key = getattr(config, "testrail_api_key", None) or os.getenv("TESTRAIL_API_KEY", "")
            if not url or not email or api_key is None:
                return {"success": False, "testrail_id": None, "error": "TestRail not configured"}
            push_enabled = getattr(config, "testrail_push_enabled", False) or os.getenv("TESTRAIL_PUSH_ENABLED", "").lower() == "true"
            if not push_enabled:
                return {"success": False, "testrail_id": None, "error": "TestRail push is disabled"}
            from backend.connectors.testrail_connector import TestRailConnector
            connector = TestRailConnector(url=url, email=email, api_key=api_key)
            priority_map = {"P0": 4, "P1": 3, "P2": 2, "P3": 1}
            priority_id = 2
            if priority:
                priority_id = priority_map.get((priority or "").upper().strip(), 2)
            type_id = None
            if case_type_name:
                try:
                    ct_map = connector._build_case_type_map()
                    type_id = next((tid for tid, name in ct_map.items() if name.lower() == case_type_name.lower()), None)
                except Exception:
                    pass
            result = connector.add_case(
                section_id=int(section_id),
                title=title.strip(),
                steps=(steps or "").strip() or None,
                expected_result=(expected_result or "").strip() or None,
                preconditions=(preconditions or "").strip() or None,
                priority_id=priority_id,
                requirement_text=(requirement_text or "").strip() or None,
                platform=(platform or "").strip() or None,
                type_id=type_id,
            )
            new_id = result.get("id") if isinstance(result, dict) else None
            testrail_id = f"C{new_id}" if new_id is not None else None
            return {"success": True, "testrail_id": testrail_id}
        except Exception as e:
            return {"success": False, "testrail_id": None, "error": str(e)}

    def ingest_pushed_case_into_rag(
        self,
        testrail_id: str,
        title: str,
        steps: Optional[str] = None,
        expected_result: Optional[str] = None,
        preconditions: Optional[str] = None,
        priority: Optional[str] = None,
        requirement_text: Optional[str] = None,
        case_type: Optional[str] = None,
    ) -> bool:
        """
        Ingest a single test case (just pushed to TestRail) into ChromaDB so it appears
        as an existing test in the next requirement analysis run.
        Uses the same CSV shape as TestRail sync (one row). If requirement_text is provided
        (e.g. from the requirement that generated this test), it is stored so retrieval
        by that requirement text finds this test.
        Logs errors but does not raise. Returns True if ingest succeeded, False otherwise.
        """
        if not testrail_id or not title or not self.rag_service:
            return False
        import tempfile
        import csv
        safe_id = re.sub(r"[^\w\-]", "_", str(testrail_id).strip())[:50]
        file_name = f"testrail_pushed_{safe_id}.csv"
        try:
            # One row matching testrail_sync_service / testrail_connector CSV structure (RAG expects these headers)
            row = {
                "ID": str(testrail_id).strip(),
                "Title": (title or "").strip() or "Test case",
                "Execution Mode": "Automatable",
                "Expected Result": (expected_result or "").strip() or "",
                "Platform": "api / backend",
                "Preconditions": (preconditions or "").strip() or "",
                "Priority": (priority or "P2").strip().upper() or "P2",
                "Section Hierarchy": "",
                "Steps": (steps or "").strip() or "",
                "Type": (case_type or "Functional"),
                "Suite": "Pushed",
            }
            if requirement_text and (requirement_text or "").strip():
                row["Requirement"] = (requirement_text or "").strip()[:2000]
            headers = list(row.keys())
            fd, path = tempfile.mkstemp(suffix=".csv", prefix="req_ingest_")
            try:
                with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
                    w = csv.DictWriter(f, fieldnames=headers)
                    w.writeheader()
                    w.writerow(row)
                result = self.rag_service.upload_document(
                    file_path=Path(path),
                    file_name=file_name,
                    subdir="testrail",
                )
                if not result.get("success"):
                    print(f"Requirement analysis: ChromaDB ingest failed for {testrail_id}: {result.get('error', 'Unknown')}")
                    return False
                return True
            finally:
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass
        except Exception as e:
            print(f"Requirement analysis: ChromaDB ingest failed for {testrail_id}: {e}")
            return False
