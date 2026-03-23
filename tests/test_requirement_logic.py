"""
Unit tests for requirement analysis core logic.
Tests _compute_generate_priorities, _coverage_sufficient_shortcut,
_compute_coverage_metrics, the similarity band split, and settings schema.

Run with: venv/bin/python -m pytest tests/test_requirement_logic.py -v
"""

import os
import sys
import importlib
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_test(priority="P0", similarity=0.85, testrail_id="TC-1"):
    """Build a minimal related-test dict (similarity as 0-1)."""
    return {
        "testrail_id": testrail_id,
        "title": f"Test {testrail_id}",
        "priority": priority,
        "similarity_score": similarity,
        "content": "some test content",
    }


def _make_test_pct(priority="P0", similarity_pct=85.0, testrail_id="TC-1"):
    """Build a related-test dict with similarity in 0-100 range."""
    return {
        "testrail_id": testrail_id,
        "title": f"Test {testrail_id}",
        "priority": priority,
        "similarity_score": similarity_pct,
        "content": "some test content",
    }


# ---------------------------------------------------------------------------
# Import functions under test (patch env before import so defaults resolve)
# ---------------------------------------------------------------------------

def _import_funcs(coverage_min_sim="80", min_tests_per_priority="3"):
    """Re-import requirement_analysis_service with specific env vars set."""
    os.environ["REQUIREMENT_COVERAGE_SUFFICIENT_MIN_SIMILARITY"] = coverage_min_sim
    os.environ["REQUIREMENT_MIN_TESTS_PER_PRIORITY"] = min_tests_per_priority
    import backend.services.requirement_analysis_service as m
    importlib.reload(m)
    return m


# ===========================================================================
# 1. Settings schema — removed field must not appear
# ===========================================================================

class TestSettingsSchema:
    def test_needs_update_confidence_threshold_removed(self):
        from backend.services.settings_service import SETTINGS_SCHEMA
        keys = [e["key"] for e in SETTINGS_SCHEMA]
        assert "requirement_needs_update_confidence_threshold" not in keys, (
            "requirement_needs_update_confidence_threshold must be removed from SETTINGS_SCHEMA"
        )

    def test_coverage_sufficient_min_similarity_present(self):
        from backend.services.settings_service import SETTINGS_SCHEMA
        keys = [e["key"] for e in SETTINGS_SCHEMA]
        assert "requirement_coverage_sufficient_min_similarity" in keys

    def test_coverage_sufficient_min_similarity_in_requirements_category(self):
        from backend.services.settings_service import SETTINGS_SCHEMA
        entry = next(e for e in SETTINGS_SCHEMA if e["key"] == "requirement_coverage_sufficient_min_similarity")
        assert entry["category"] == "requirements"
        assert entry["type"] == "number"
        assert entry["min"] == 0
        assert entry["max"] == 100

    def test_requirements_tab_has_correct_keys(self):
        from backend.services.settings_service import SETTINGS_SCHEMA
        req_keys = {e["key"] for e in SETTINGS_SCHEMA if e["category"] == "requirements"}
        expected = {
            "requirement_retrieval_k",
            "requirement_retrieval_similarity_threshold",
            "requirement_use_hybrid_search",
            "requirement_use_reranking",
            "requirement_min_tests_per_priority",
            "requirement_coverage_sufficient_min_similarity",
        }
        assert req_keys == expected


# ===========================================================================
# 2. _assess_updates signature — must NOT have confidence_threshold param
# ===========================================================================

class TestAssessUpdatesSignature:
    def test_no_confidence_threshold_param(self):
        import inspect
        import backend.services.requirement_analysis_service as m
        sig = inspect.signature(m.RequirementAnalysisService._assess_updates)
        assert "confidence_threshold" not in sig.parameters, (
            "_assess_updates must not have confidence_threshold parameter"
        )

    def test_has_expected_params(self):
        import inspect
        import backend.services.requirement_analysis_service as m
        sig = inspect.signature(m.RequirementAnalysisService._assess_updates)
        params = list(sig.parameters.keys())
        assert "self" in params
        assert "requirement_text" in params
        assert "related_tests" in params
        assert "run_id" in params
        assert "run_cost" in params


# ===========================================================================
# 3. _compute_generate_priorities (Gate 1)
# ===========================================================================

class TestComputeGeneratePriorities:

    def setup_method(self):
        self.m = _import_funcs(coverage_min_sim="80", min_tests_per_priority="3")
        self.fn = self.m._compute_generate_priorities

    # -- Basic: enough strong tests → nothing to generate --
    def test_no_generation_needed_when_fully_covered(self):
        tests = [
            _make_test("P0", 0.90, "TC-1"),
            _make_test("P0", 0.92, "TC-2"),
            _make_test("P0", 0.88, "TC-3"),
            _make_test("P1", 0.85, "TC-4"),
            _make_test("P1", 0.86, "TC-5"),
            _make_test("P1", 0.87, "TC-6"),
        ]
        result = self.fn(tests, generate_p2_p3=False)
        assert result == [], f"Expected no priorities to generate, got {result}"

    # -- Weak tests (below coverage threshold) don't count --
    def test_weak_tests_do_not_count_toward_coverage(self):
        # 3 P0 tests all below 80% → should still generate P0
        tests = [
            _make_test("P0", 0.75, "TC-1"),  # 75% < 80% → weak
            _make_test("P0", 0.78, "TC-2"),
            _make_test("P0", 0.79, "TC-3"),
        ]
        result = self.fn(tests, generate_p2_p3=False)
        assert "P0" in result

    # -- Exactly at threshold counts --
    def test_tests_at_exact_threshold_count(self):
        # 3 P0 at exactly 80% → should count (>= 80)
        tests = [
            _make_test("P0", 0.80, "TC-1"),
            _make_test("P0", 0.80, "TC-2"),
            _make_test("P0", 0.80, "TC-3"),
            _make_test("P1", 0.80, "TC-4"),
            _make_test("P1", 0.80, "TC-5"),
            _make_test("P1", 0.80, "TC-6"),
        ]
        result = self.fn(tests, generate_p2_p3=False)
        assert result == []

    # -- Mixed: P0 covered, P1 not → only P1 generated --
    def test_partial_coverage_generates_only_missing_priorities(self):
        tests = [
            _make_test("P0", 0.90, "TC-1"),
            _make_test("P0", 0.91, "TC-2"),
            _make_test("P0", 0.92, "TC-3"),
            _make_test("P1", 0.75, "TC-4"),  # weak — won't count
            _make_test("P1", 0.76, "TC-5"),  # weak
        ]
        result = self.fn(tests, generate_p2_p3=False)
        assert "P0" not in result
        assert "P1" in result

    # -- P2/P3 only generated when generate_p2_p3=True --
    def test_p2_p3_not_generated_when_flag_false(self):
        tests = []  # no tests at all
        result = self.fn(tests, generate_p2_p3=False)
        assert "P2" not in result
        assert "P3" not in result

    def test_p2_p3_generated_when_flag_true_and_no_tests(self):
        result = self.fn([], generate_p2_p3=True)
        assert "P2" in result
        assert "P3" in result

    # -- ok_ids (LLM-validated tests from the needs-update band) also count --
    def test_ok_ids_count_toward_coverage(self):
        # 2 strong P0 tests + 1 LLM-validated ok (was in needs-update band)
        tests = [
            _make_test("P0", 0.90, "TC-1"),
            _make_test("P0", 0.91, "TC-2"),
            _make_test("P0", 0.77, "TC-3"),  # weak similarity BUT LLM said ok
        ]
        result = self.fn(tests, generate_p2_p3=False, ok_ids=["TC-3"])
        # TC-3 counted via ok_ids → total P0 = 3 → no generation
        assert "P0" not in result

    # -- Similarity in 0-100 range (not 0-1) is handled correctly --
    def test_similarity_in_0_100_range(self):
        tests = [
            _make_test_pct("P0", 90.0, "TC-1"),
            _make_test_pct("P0", 88.0, "TC-2"),
            _make_test_pct("P0", 85.0, "TC-3"),
            _make_test_pct("P1", 82.0, "TC-4"),
            _make_test_pct("P1", 83.0, "TC-5"),
            _make_test_pct("P1", 84.0, "TC-6"),
        ]
        result = self.fn(tests, generate_p2_p3=False)
        assert result == []

    # -- Empty tests → generate all requested priorities --
    def test_empty_tests_generates_all_priorities(self):
        result = self.fn([], generate_p2_p3=True)
        assert set(result) == {"P0", "P1", "P2", "P3"}

    # -- min_tests_per_priority respected --
    def test_min_tests_per_priority_env_var(self):
        m = _import_funcs(coverage_min_sim="80", min_tests_per_priority="5")
        fn = m._compute_generate_priorities
        # Only 3 strong P0 tests → with min=5, still needs generation
        tests = [
            _make_test("P0", 0.90, "TC-1"),
            _make_test("P0", 0.91, "TC-2"),
            _make_test("P0", 0.92, "TC-3"),
        ]
        result = fn(tests, generate_p2_p3=False)
        assert "P0" in result

    def test_min_tests_per_priority_2_satisfied_by_2_tests(self):
        m = _import_funcs(coverage_min_sim="80", min_tests_per_priority="2")
        fn = m._compute_generate_priorities
        tests = [
            _make_test("P0", 0.90, "TC-1"),
            _make_test("P0", 0.91, "TC-2"),
            _make_test("P1", 0.85, "TC-3"),
            _make_test("P1", 0.86, "TC-4"),
        ]
        result = fn(tests, generate_p2_p3=False)
        assert result == []


# ===========================================================================
# 4. _coverage_sufficient_shortcut (fast-path, no LLM needed)
# ===========================================================================

class TestCoverageSufficientShortcut:

    def setup_method(self):
        self.m = _import_funcs(coverage_min_sim="80", min_tests_per_priority="3")
        self.fn = self.m._coverage_sufficient_shortcut

    def test_sufficient_when_all_tests_above_threshold(self):
        tests = [_make_test("P0", 0.90, f"TC-{i}") for i in range(5)]
        assert self.fn(tests) is True

    def test_insufficient_when_any_test_below_threshold(self):
        tests = [_make_test("P0", 0.90, f"TC-{i}") for i in range(4)]
        tests.append(_make_test("P0", 0.70, "TC-weak"))  # 70% < 80%
        assert self.fn(tests) is False

    def test_insufficient_when_fewer_than_min_tests(self):
        # Only 4 tests, default min_tests=5 in shortcut
        tests = [_make_test("P0", 0.90, f"TC-{i}") for i in range(4)]
        assert self.fn(tests) is False

    def test_insufficient_when_no_similarity_score(self):
        tests = [{"testrail_id": "TC-1", "priority": "P0"}] * 6  # no similarity_score
        assert self.fn(tests) is False

    def test_empty_list_returns_false(self):
        assert self.fn([]) is False

    def test_too_few_tests_returns_false(self):
        tests = [_make_test("P0", 0.95, f"TC-{i}") for i in range(2)]
        assert self.fn(tests) is False  # len < 3 early exit


# ===========================================================================
# 5. Similarity band split logic (need-update vs reuse-as-is)
# ===========================================================================

class TestSimilarityBandSplit:
    """
    Simulate the band-split loop that runs inside analyze_requirements.
    retrieval_threshold <= sim <= coverage_min → "Needs Update" band
    sim > coverage_min → "Reuse as-is"
    """

    def _split(self, tests, retrieval_pct=75.0, coverage_min_pct=80.0):
        need_update_band = []
        reuse_band = []
        for t in tests:
            s = t.get("similarity_score")
            if s is None:
                reuse_band.append(t)
                continue
            spct = (s * 100.0) if s <= 1.0 else float(s)
            if retrieval_pct <= spct <= coverage_min_pct:
                need_update_band.append(t)
            else:
                reuse_band.append(t)
        return need_update_band, reuse_band

    def test_test_below_retrieval_goes_to_reuse(self):
        # Similarity below retrieval threshold → would not have been retrieved,
        # but if it slips through it lands in reuse (not needs-update)
        t = _make_test("P0", 0.70)  # 70% < 75%
        nu, r = self._split([t])
        assert t in r
        assert t not in nu

    def test_test_in_band_goes_to_needs_update(self):
        t = _make_test("P0", 0.77)  # 77% → 75 <= 77 <= 80
        nu, r = self._split([t])
        assert t in nu
        assert t not in r

    def test_test_at_lower_boundary_goes_to_needs_update(self):
        t = _make_test("P0", 0.75)  # exactly at retrieval threshold
        nu, r = self._split([t])
        assert t in nu

    def test_test_at_upper_boundary_goes_to_needs_update(self):
        t = _make_test("P0", 0.80)  # exactly at coverage_min → still in band (<=)
        nu, r = self._split([t])
        assert t in nu

    def test_test_above_coverage_min_goes_to_reuse(self):
        t = _make_test("P0", 0.85)  # 85% > 80%
        nu, r = self._split([t])
        assert t in r
        assert t not in nu

    def test_test_with_no_similarity_goes_to_reuse(self):
        t = {"testrail_id": "TC-X", "priority": "P0"}
        nu, r = self._split([t])
        assert t in r
        assert t not in nu

    def test_mixed_tests_split_correctly(self):
        reuse1 = _make_test("P0", 0.90, "TC-reuse1")
        needs  = _make_test("P0", 0.77, "TC-needs")
        reuse2 = _make_test("P0", 0.60, "TC-reuse2")  # below retrieval
        nu, r = self._split([reuse1, needs, reuse2])
        assert needs in nu
        assert reuse1 in r
        assert reuse2 in r

    def test_0_to_100_similarity_range_also_splits_correctly(self):
        reuse = _make_test_pct("P0", 90.0, "TC-r")
        needs = _make_test_pct("P0", 77.0, "TC-n")
        nu, r = self._split([reuse, needs])
        assert needs in nu
        assert reuse in r

    def test_coverage_min_equal_retrieval_threshold_puts_all_at_boundary_in_band(self):
        # When both thresholds are the same (e.g. 80), only tests exactly at 80% are in band
        t_exact = _make_test("P0", 0.80, "TC-exact")
        t_above = _make_test("P0", 0.81, "TC-above")
        nu, r = self._split([t_exact, t_above], retrieval_pct=80.0, coverage_min_pct=80.0)
        assert t_exact in nu   # 80 <= 80 <= 80
        assert t_above in r    # 81 > 80


# ===========================================================================
# 6. _compute_coverage_metrics
# ===========================================================================

class TestComputeCoverageMetrics:

    def setup_method(self):
        self.m = _import_funcs(coverage_min_sim="80", min_tests_per_priority="3")
        self.fn = self.m._compute_coverage_metrics

    def test_100_percent_coverage_when_all_priorities_met(self):
        tests = [
            _make_test("P0", 0.90, "TC-1"),
            _make_test("P0", 0.91, "TC-2"),
            _make_test("P0", 0.92, "TC-3"),
            _make_test("P1", 0.85, "TC-4"),
            _make_test("P1", 0.86, "TC-5"),
            _make_test("P1", 0.87, "TC-6"),
        ]
        metrics = self.fn(tests, generated_tests_for_req=[], generate_p2_p3=False)
        assert metrics.get("final_coverage_pct") == 100
        assert metrics.get("status") == "covered"

    def test_0_percent_coverage_when_no_tests(self):
        metrics = self.fn([], generated_tests_for_req=[], generate_p2_p3=False)
        assert metrics.get("final_coverage_pct") == 0
        assert metrics.get("status") == "uncovered"

    def test_generated_tests_contribute_to_coverage(self):
        existing = [
            _make_test("P0", 0.90, "TC-1"),
            _make_test("P0", 0.91, "TC-2"),
            _make_test("P0", 0.92, "TC-3"),
        ]
        # P1 has no existing tests; generated tests fill the gap
        generated = [
            {"priority": "P1", "title": "Generated P1-1"},
            {"priority": "P1", "title": "Generated P1-2"},
            {"priority": "P1", "title": "Generated P1-3"},
        ]
        metrics = self.fn(existing, generated_tests_for_req=generated, generate_p2_p3=False)
        assert metrics.get("final_coverage_pct") == 100
        assert metrics.get("status") == "covered"

    def test_weak_tests_do_not_count_as_strong_in_metrics(self):
        tests = [
            _make_test("P0", 0.70, "TC-1"),  # 70% < 80% → weak, not in existing_counts
            _make_test("P0", 0.72, "TC-2"),
            _make_test("P0", 0.74, "TC-3"),
        ]
        metrics = self.fn(tests, generated_tests_for_req=[], generate_p2_p3=False)
        # Weak tests don't count as "strong" coverage → existing_coverage_pct = 0
        assert metrics.get("existing_coverage_pct") == 0
        # P0 has 0 strong tests → not "covered", status is not "covered"
        assert metrics.get("status") != "covered"
        # P0 by_priority existing count = 0 (weak tests don't count)
        p0 = metrics["by_priority"]["P0"]
        assert p0["existing"] == 0


# ===========================================================================
# 7. Integration: REQUIREMENT_NEEDS_UPDATE_CONFIDENCE_THRESHOLD no longer read
# ===========================================================================

class TestRemovedEnvVarNotUsed:

    def test_old_env_var_not_read_by_analyze_function(self):
        """
        Set REQUIREMENT_NEEDS_UPDATE_CONFIDENCE_THRESHOLD to a nonsense value.
        The band split should use REQUIREMENT_COVERAGE_SUFFICIENT_MIN_SIMILARITY
        instead. We verify by inspecting the source code directly.
        """
        import backend.services.requirement_analysis_service as m
        import inspect
        src = inspect.getsource(m)
        # Old env var must not be read by the service anymore
        assert "REQUIREMENT_NEEDS_UPDATE_CONFIDENCE_THRESHOLD" not in src, (
            "REQUIREMENT_NEEDS_UPDATE_CONFIDENCE_THRESHOLD should no longer be "
            "referenced in requirement_analysis_service.py"
        )

    def test_coverage_min_similarity_env_var_controls_band(self):
        """REQUIREMENT_COVERAGE_SUFFICIENT_MIN_SIMILARITY must appear in the source."""
        import backend.services.requirement_analysis_service as m
        import inspect
        src = inspect.getsource(m)
        assert "REQUIREMENT_COVERAGE_SUFFICIENT_MIN_SIMILARITY" in src


# ===========================================================================
# 8. AC extraction wiring — _compute_generate_priorities with acceptance_criteria
# ===========================================================================

class TestACExtraction:
    """Tests for the acceptance_criteria parameter added to _compute_generate_priorities."""

    def setup_method(self):
        m = _import_funcs(coverage_min_sim="75", min_tests_per_priority="3")
        self.fn = m._compute_generate_priorities

    def test_no_acs_uses_default_min_per_priority(self):
        """Without ACs, behaviour is identical to before."""
        tests = [
            _make_test("P0", 0.85, f"TC-{i}") for i in range(3)
        ] + [
            _make_test("P1", 0.85, f"TC-P1-{i}") for i in range(3)
        ]
        result = self.fn(tests, generate_p2_p3=False, acceptance_criteria=None)
        assert result == []  # 3 P0 + 3 P1 >= min_per_priority=3 → nothing to generate

    def test_many_acs_raises_threshold_above_default(self):
        """When len(ACs) > min_per_priority, need that many tests to pass Gate 1."""
        # 5 ACs → effective threshold becomes 5, but we only have 3 P0 tests
        acs = ["AC1", "AC2", "AC3", "AC4", "AC5"]
        tests = [_make_test("P0", 0.85, f"TC-{i}") for i in range(3)]
        result = self.fn(tests, generate_p2_p3=False, acceptance_criteria=acs)
        assert "P0" in result  # 3 tests < 5 ACs → P0 still needs generation

    def test_enough_tests_for_all_acs_passes_gate1(self):
        """If eligible tests >= AC count per priority, Gate 1 passes."""
        acs = ["AC1", "AC2", "AC3", "AC4", "AC5"]
        # 5 P0 tests + 5 P1 tests, all strong similarity
        tests = (
            [_make_test("P0", 0.85, f"TC-P0-{i}") for i in range(5)] +
            [_make_test("P1", 0.85, f"TC-P1-{i}") for i in range(5)]
        )
        result = self.fn(tests, generate_p2_p3=False, acceptance_criteria=acs)
        assert result == []  # 5 per priority >= 5 ACs → covered

    def test_fewer_acs_than_min_priority_uses_min(self):
        """When len(ACs) <= min_per_priority, default threshold still applies."""
        acs = ["AC1", "AC2"]  # only 2 ACs → threshold stays at min_per_priority=3
        tests = [_make_test("P0", 0.85, f"TC-{i}") for i in range(2)]
        result = self.fn(tests, generate_p2_p3=False, acceptance_criteria=acs)
        assert "P0" in result  # 2 tests < min_per_priority=3 → still needs generation

    def test_ac_extraction_method_exists(self):
        """_extract_acceptance_criteria method must exist on the service class."""
        from backend.services.requirement_analysis_service import RequirementAnalysisService
        assert hasattr(RequirementAnalysisService, "_extract_acceptance_criteria"), (
            "_extract_acceptance_criteria method must be defined on RequirementAnalysisService"
        )

    def test_ac_extraction_method_signature(self):
        """_extract_acceptance_criteria must accept req_title and req_desc."""
        import inspect
        from backend.services.requirement_analysis_service import RequirementAnalysisService
        sig = inspect.signature(RequirementAnalysisService._extract_acceptance_criteria)
        params = list(sig.parameters.keys())
        assert "req_title" in params
        assert "req_desc" in params

    def test_retrieval_query_uses_short_desc(self):
        """Verify source code uses _retrieval_query (not req_text) for find_related_tests."""
        import inspect
        import backend.services.requirement_analysis_service as m
        src = inspect.getsource(m)
        assert "_retrieval_query" in src, "_retrieval_query variable must exist for focused retrieval"
        assert "find_related_tests(_retrieval_query" in src, "find_related_tests must use _retrieval_query not req_text"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
