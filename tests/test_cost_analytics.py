"""Tests for cost_tracker's rate card / model resolution / duration, and for the
analytics aggregator that reads the records back."""

import json
import time
from datetime import datetime
from pathlib import Path

import pytest

from backend import cost_tracker as ct
from backend.services import analytics_service as asvc


class _Result:
    """Stand-in for a LangChain AIMessage."""
    def __init__(self, model=None, usage=None, meta=None):
        self.usage_metadata = usage
        self.response_metadata = meta if meta is not None else (
            {"model_name": model} if model else {})


@pytest.fixture
def costs(tmp_path, monkeypatch):
    path = tmp_path / "operation_costs.jsonl"
    monkeypatch.setenv("OPERATION_COSTS_FILE", str(path))
    monkeypatch.delenv("LLM_COST_RATES_JSON", raising=False)
    return path


def _rows(path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


# ── rate card ─────────────────────────────────────────────────────────────────

def test_longest_model_match_wins():
    """gpt-4o-mini must not be priced as gpt-4o."""
    assert ct.rates_for_model("gpt-4o-mini") == (0.15, 0.60)
    assert ct.rates_for_model("gpt-4o-2024-08-06") == (2.50, 10.00)


def test_local_models_are_free():
    """The live bug this fixes: one global rate pair billed a local Ollama run
    at GPT-4o rates, inventing spend that never happened."""
    for model in ("llama3.2:3b", "mistral:7b", "qwen2.5", "gemma2"):
        assert ct.rates_for_model(model) == (0.0, 0.0), model


def test_local_embedding_models_are_free():
    """Observed live: all-MiniLM-L6-v2 runs on the machine but was billed at the
    GPT-4o fallback rate, inventing spend that never happened."""
    for model in ("sentence-transformers/all-MiniLM-L6-v2", "BAAI/bge-small-en",
                  "intfloat/e5-base", "nomic-embed-text"):
        assert ct.rates_for_model(model) == (0.0, 0.0), model


def test_flash_lite_is_not_priced_as_flash():
    assert ct.rates_for_model("gemini-2.5-flash") == (0.30, 2.50)
    assert ct.rates_for_model("gemini-2.5-flash-lite") == (0.10, 0.40)


def test_unknown_model_falls_back_to_the_global_pair(monkeypatch):
    monkeypatch.setenv("LLM_COST_INPUT_PER_1M", "7.0")
    monkeypatch.setenv("LLM_COST_OUTPUT_PER_1M", "21.0")
    assert ct.rates_for_model("some-model-shipped-tomorrow") == (7.0, 21.0)
    assert ct.rates_for_model(None) == (7.0, 21.0)


def test_rate_overrides_from_env(monkeypatch):
    monkeypatch.setenv("LLM_COST_RATES_JSON", json.dumps({"gpt-4o": [1.0, 2.0]}))
    assert ct.rates_for_model("gpt-4o") == (1.0, 2.0)


def test_embedding_models_bill_input_only():
    """An embedding model must not inherit a chat model's output rate."""
    for model in ("text-embedding-3-small", "text-embedding-3-large"):
        assert ct.rates_for_model(model)[1] == 0.0, model


def test_ollama_run_costs_nothing(costs):
    ct.record_operation("rag.query", input_tokens=100_000, output_tokens=50_000,
                        model="llama3.2:3b")
    assert _rows(costs)[0]["estimated_cost_usd"] == 0.0


# ── model resolution ──────────────────────────────────────────────────────────

def test_model_is_read_from_the_response_not_left_null(costs):
    """model was null on 100% of the 4,843 pre-existing records because no call
    site passed it. Reading it from the result fixes every site at once."""
    ct.record_from_langchain_result(
        "requirement_analysis.generate_tests",
        _Result(model="gpt-4o-mini", usage={"input_tokens": 10, "output_tokens": 20}))
    row = _rows(costs)[0]
    assert row["model"] == "gpt-4o-mini"
    assert row["estimated_cost_usd"] == pytest.approx(
        10 / 1e6 * 0.15 + 20 / 1e6 * 0.60)


def test_response_model_beats_the_caller_supplied_one(costs):
    ct.record_from_langchain_result("op", _Result(model="gpt-4o-mini"),
                                    model="gpt-4o")
    assert _rows(costs)[0]["model"] == "gpt-4o-mini"


def test_missing_model_metadata_is_tolerated(costs):
    ct.record_from_langchain_result("op", _Result())
    assert _rows(costs)[0]["model"] is None


# ── stored rates ──────────────────────────────────────────────────────────────

def test_rates_are_stored_on_the_record(costs):
    ct.record_operation("op", input_tokens=1, output_tokens=1, model="gpt-4o")
    row = _rows(costs)[0]
    assert (row["rate_in_per_1m"], row["rate_out_per_1m"]) == (2.50, 10.00)


def test_editing_the_rate_card_does_not_rewrite_history(costs, monkeypatch):
    ct.record_operation("op", input_tokens=1_000_000, output_tokens=0, model="gpt-4o")
    original = _rows(costs)[0]
    monkeypatch.setenv("LLM_COST_RATES_JSON", json.dumps({"gpt-4o": [99.0, 99.0]}))
    ct.record_operation("op", input_tokens=1_000_000, output_tokens=0, model="gpt-4o")
    after = _rows(costs)
    assert after[0]["estimated_cost_usd"] == original["estimated_cost_usd"] == 2.50
    assert after[1]["estimated_cost_usd"] == 99.0   # only the new row reprices


# ── duration ──────────────────────────────────────────────────────────────────

def test_duration_is_recorded(costs):
    ct.record_operation("op", input_tokens=1, duration_s=1.2345)
    assert _rows(costs)[0]["duration_s"] == 1.234 or _rows(costs)[0]["duration_s"] == 1.235


def test_timed_operation_records_even_when_the_call_raises(costs):
    """A call that raises still consumed wall time; recording it is what stops a
    failing operation looking free."""
    with pytest.raises(RuntimeError):
        with ct.timed_operation("op.boom") as slot:
            raise RuntimeError("upstream died")
    row = _rows(costs)[0]
    assert row["operation"] == "op.boom"
    assert row["duration_s"] >= 0


def test_timed_operation_records_usage_on_success(costs):
    with ct.timed_operation("op.ok", run_id="r1") as slot:
        slot["result"] = _Result(model="gpt-4o",
                                 usage={"input_tokens": 5, "output_tokens": 5})
    row = _rows(costs)[0]
    assert row["run_id"] == "r1" and row["model"] == "gpt-4o"
    assert row["duration_s"] is not None


# ── aggregator ────────────────────────────────────────────────────────────────

@pytest.fixture
def seeded(tmp_path, monkeypatch):
    costs = tmp_path / "operation_costs.jsonl"
    runs = tmp_path / "requirement_runs.jsonl"
    monkeypatch.setenv("OPERATION_COSTS_FILE", str(costs))
    monkeypatch.setenv("REQUIREMENT_RUNS_FILE", str(runs))
    now = time.time()
    lines = []
    for i in range(3):
        lines.append(json.dumps({
            "operation": "requirement_analysis.generate_tests",
            "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%S+00:00",
                                           time.gmtime(now - 60 + i * 10)),
            "input_tokens": 100, "output_tokens": 200,
            "estimated_cost_usd": 0.5, "model": "gpt-4o", "run_id": "run-a"}))
    # A chat call: real spend, never part of a run.
    lines.append(json.dumps({
        "operation": "rag.query",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now)),
        "input_tokens": 10, "output_tokens": 10, "estimated_cost_usd": 0.25,
        "model": "gpt-4o"}))
    costs.write_text("\n".join(lines) + "\n{truncated")
    return costs, runs


def test_custom_range_clear_removes_only_rows_inside_it(seeded):
    costs, _ = seeded
    before = asvc.query("all")["overall"]["calls"]
    now = time.time()
    # Seeded ops sit at now-60, -50, -40 and now: only the -50 one is inside.
    asvc.clear_analytics(window="custom", since=now - 55, until=now - 45)
    assert asvc.query("all")["overall"]["calls"] == before - 1


def test_aggregator_totals_and_grouping(seeded):
    q = asvc.query("all")
    assert q["overall"]["calls"] == 4          # the truncated line is skipped
    assert q["overall"]["cost_usd"] == 1.75
    # Runs are counted from requirement_runs.jsonl summary rows. run-a has spend
    # but no summary row, so its spend counts and it adds no run or time.
    assert q["overall"]["runs"] == 0
    assert q["run_duration_s"] == 0
    # Not a recorded run, so not "requirements" (the runs the section counts)
    # and no stage bar; still spend, as requirements_other.
    assert q["by_group"]["requirements_other"]["calls"] == 3
    assert "requirements" not in q["by_group"] or q["by_group"]["requirements"]["calls"] == 0
    assert q["by_stage"] == {}
    assert q["by_group"]["ask"]["calls"] == 1
    assert q["by_group"]["ask"]["questions"] == 1
    assert q["cost_basis"] == "estimated"


def test_null_tokens_do_not_crash_the_summer(tmp_path, monkeypatch):
    path = tmp_path / "operation_costs.jsonl"
    monkeypatch.setenv("OPERATION_COSTS_FILE", str(path))
    path.write_text(json.dumps({
        "operation": "rag.query", "timestamp_utc": "2026-08-28T12:00:00+00:00",
        "input_tokens": None, "output_tokens": None,
        "estimated_cost_usd": 0.1, "run_id": "x"}) + "\n")
    assert asvc.query("all")["overall"]["input_tokens"] == 0


def test_run_summary_round_trip(seeded):
    _, runs = seeded
    assert asvc.record_requirement_run(
        {"run_id": "run-a", "requirements_analyzed": 3,
         "total_estimated_cost_usd": 1.5, "duration_s": 42.0,
         "stage_timings": [{"stage": 1, "duration_s": 10.0, "cost_usd": 0.5}],
         "summary": {"total_generated_tests": 7, "e2e_workflow_tests_count": 2,
                     "requirements_with_coverage": 2, "uncovered_count": 1}},
        run_id="run-a", status="completed", source_type="confluence",
        started_at=time.time() - 42)
    q = asvc.query("all")
    assert q["runs_summarised"] == 1
    assert q["outcomes"]["test_cases_generated"] == 7
    assert q["outcomes"]["e2e_tests_generated"] == 2
    # Recorded now, so its calls are the section's and fill the stage bars.
    assert q["by_group"]["requirements"]["calls"] == 3
    assert q["by_stage"]["3"]["calls"] == 3    # generate_tests is a stage-3 op


def test_time_taken_is_what_the_trend_adds_up_to(seeded, tmp_path, monkeypatch):
    """Run wall time + each question's calls + each sync, per day. Sync
    timestamps are local time (datetime.now().isoformat()), not UTC."""
    asvc.record_requirement_run({"run_id": "run-a", "duration_s": 42.0}, run_id="run-a",
                                status="completed", started_at=time.time() - 42)
    monkeypatch.setattr(asvc, "_PROJECT_ROOT", tmp_path)
    (tmp_path / "storage").mkdir(exist_ok=True)
    (tmp_path / "storage" / "testrail_sync_metadata.json").write_text(json.dumps({"syncs": [
        {"timestamp": datetime.fromtimestamp(time.time() - 3600).isoformat(), "duration_seconds": 30}]}))
    q = asvc.query("24h")
    assert q["ingestion"]["count"] == 1     # read as UTC it sat hours away, east of UTC
    assert q["time_taken_s"] == pytest.approx(42.0 + 30 + q["by_group"]["ask"]["duration_s"])
    assert sum(p["duration_s"] for p in q["series"]) == pytest.approx(q["time_taken_s"])


def test_failed_run_still_records_its_spend(seeded):
    assert asvc.record_requirement_run(
        {"run_id": "boom", "total_estimated_cost_usd": 0.9},
        run_id="boom", status="failed", error="upstream died",
        started_at=time.time() - 5)
    q = asvc.query("all")
    assert q["runs_summarised"] == 1


def test_requirements_rollup_matches_its_series(seeded):
    """The QA Agents tab adds this rollup to the agents' totals and its series to
    the agents' trend: runs are verdict-only, and the series sums to the rollup."""
    for rid, status, cost in (("ok", "completed", 1.0), ("bad", "failed", 0.5),
                              ("stop", "cancelled", 0.25)):
        asvc.record_requirement_run(
            {"run_id": rid, "total_estimated_cost_usd": cost, "duration_s": 10.0,
             "summary": {"total_generated_tests": 2, "e2e_workflow_tests_count": 1}},
            run_id=rid, status=status, started_at=time.time() - 10)
    req = asvc.query("all")["requirements"]
    assert (req["runs"], req["succeeded"], req["failed"]) == (2, 1, 1)
    assert req["cost_usd"] == 1.75 and req["duration_s"] == 30.0   # cancelled still costs
    assert req["tests_generated"] == 9
    series = asvc.query("all")["requirements_series"]
    for key in ("runs", "cost_usd", "duration_s", "tests_generated"):
        assert sum(p[key] for p in series) == pytest.approx(req[key])


def test_missing_files_return_empty_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("OPERATION_COSTS_FILE", str(tmp_path / "nope.jsonl"))
    monkeypatch.setenv("REQUIREMENT_RUNS_FILE", str(tmp_path / "nope2.jsonl"))
    assert asvc.query("7d")["overall"]["calls"] == 0


# ── time saved ────────────────────────────────────────────────────────────────

class _Svc:
    """Minimal stand-in for SettingsService.get."""
    def __init__(self, values): self._v = values
    def get(self, key, default=None): return self._v.get(key, default)


def test_time_saved_arithmetic():
    """A healing run that fixed 2 tests in 66s, at the 45 min/test baseline:
    2 x 45 - 1.1 = 88.9 minutes."""
    from backend.api.admin import routes
    baselines = {'min_per_test_authored': 120, 'min_per_test_fixed': 45,
                 'min_per_test_adapted': 30, 'min_per_test_case_written': 15}
    healing = {'tests_created': 0, 'tests_fixed': 2, 'items_adapted': 0, 'duration_s': 66}
    agents = {'overall': healing, 'by_agent': {'test-healing-agent': healing}}
    out = routes._time_saved(agents, {}, baselines)
    assert out['agents_min'] == pytest.approx(88.9, abs=0.05)


def test_time_saved_tile_is_the_sum_of_its_rows():
    """An agent whose run time exceeds its output shows 0 in its row, so it
    must not pull the tile below the rows it sits over (18.8 h vs 18.9 h)."""
    from backend.api.admin import routes
    baselines = {'min_per_test_authored': 120, 'min_per_test_fixed': 45,
                 'min_per_test_adapted': 30, 'min_per_test_case_written': 15}
    by_agent = {'test-healing-agent': {'tests_fixed': 2, 'duration_s': 0},
                'test-adaptation-agent': {'items_adapted': 0, 'duration_s': 600}}
    out = routes._time_saved({'overall': {}, 'by_agent': by_agent}, {}, baselines)
    assert out['by_agent'] == {'test-healing-agent': 90.0, 'test-adaptation-agent': 0.0}
    assert out['agents_min'] == 90.0 and out['total_min'] == 90.0


def test_time_saved_per_day_floors_each_day():
    """The trend's per-day values: each agent-day floored at 0 on its own (a
    wasted day shows 0, not a dip), test-design-agent from its run series."""
    from backend.api.admin import routes
    baselines = {'min_per_test_authored': 120, 'min_per_test_fixed': 45,
                 'min_per_test_adapted': 30, 'min_per_test_case_written': 15}
    days = [{'bucket': '2026-09-01', 'tests_fixed': 2, 'duration_s': 0},
            {'bucket': '2026-09-02', 'tests_fixed': 0, 'duration_s': 600}]
    studio = {'requirements_series': [{'bucket': '2026-09-01', 'tests_generated': 4, 'duration_s': 60}]}
    out = routes._time_saved({'overall': {}, 'by_agent': {}, 'series_by_agent': {'test-healing-agent': days}},
                             studio, baselines)
    assert out['by_agent_day']['test-healing-agent'] == {'2026-09-01': 90.0, '2026-09-02': 0.0}
    assert out['by_agent_day']['test-design-agent'] == {'2026-09-01': 59.0}   # 4 x 15 - 1


def test_time_saved_counts_both_halves():
    from backend.api.admin import routes
    baselines = {'min_per_test_authored': 120, 'min_per_test_fixed': 45,
                 'min_per_test_adapted': 30, 'min_per_test_case_written': 15}
    authoring = {'tests_created': 1, 'tests_fixed': 0, 'items_adapted': 0, 'duration_s': 0}
    agents = {'overall': authoring, 'by_agent': {'test-authoring-agent': authoring}}
    studio = {'outcomes': {'test_cases_generated': 4, 'e2e_tests_generated': 2},
              'run_duration_s': 0}
    out = routes._time_saved(agents, studio, baselines)
    assert out['agents_min'] == 120          # 1 x 120
    assert out['studio_min'] == 90           # (4 + 2) x 15
    assert out['total_min'] == 210
    # An estimate must be labelled as one wherever it is rendered.
    assert out['basis'] == 'estimate'


def test_time_saved_never_goes_below_zero():
    """A run that produced nothing still burned machine time, but "time saved"
    is reported as an estimate of human time replaced, clamped at zero — the
    spend and duration tiles are where an unproductive run shows up."""
    from backend.api.admin import routes
    baselines = {'min_per_test_authored': 120, 'min_per_test_fixed': 45,
                 'min_per_test_adapted': 30, 'min_per_test_case_written': 15}
    idle = {'tests_created': 0, 'tests_fixed': 0, 'items_adapted': 0, 'duration_s': 1800}
    agents = {'overall': idle, 'by_agent': {'test-healing-agent': idle}}
    out = routes._time_saved(agents, {}, baselines)
    assert out['agents_min'] == 0.0


def test_time_saved_per_agent_uses_the_same_rule():
    """The breakdown table reads these; it no longer re-derives them in JS."""
    from backend.api.admin import routes
    baselines = {'min_per_test_authored': 120, 'min_per_test_fixed': 45,
                 'min_per_test_adapted': 30, 'min_per_test_case_written': 15}
    agents = {'overall': {}, 'by_agent': {
        'test-adaptation-agent': {'items_adapted': 5, 'duration_s': 6240},   # 150 - 104
        'test-triaging-agent': {'duration_s': 600},                         # floored
    }}
    out = routes._time_saved(agents, {}, baselines)
    assert out['by_agent'] == {'test-adaptation-agent': 46.0, 'test-triaging-agent': 0.0}


def test_baselines_fall_back_to_defaults_when_unset(monkeypatch):
    from backend.api.admin import routes
    import flask
    app = flask.Flask(__name__)
    app.config['SETTINGS_SERVICE'] = _Svc({})
    with app.app_context():
        b = routes._analytics_baselines()
    assert b['min_per_test_authored'] == 240
    assert b['min_per_test_fixed'] == 60
    assert b['min_per_test_adapted'] == 150
    assert b['min_per_test_case_written'] == 15


def test_baselines_read_from_settings():
    from backend.api.admin import routes
    import flask
    app = flask.Flask(__name__)
    app.config['SETTINGS_SERVICE'] = _Svc({'analytics_min_per_test_fixed': '90'})
    with app.app_context():
        b = routes._analytics_baselines()
    assert b['min_per_test_fixed'] == 90.0


# ── stage re-entry (Requirements → Tests) ─────────────────────────────────────

def _run_stage_sequence(sequence):
    """Drive the real analyze() stage bookkeeping over a stage sequence.

    Stages are not visited once each: the per-requirement loop reports stage 3
    then stage 2 for every requirement, so both re-open many times per run.
    """
    from backend.services.requirement_analysis_service import RequirementAnalysisService
    import inspect as _inspect
    src = _inspect.getsource(RequirementAnalysisService.analyze)
    assert "stage_totals" in src, "analyze() must accumulate per stage, not append fragments"

    # Mirror of the accumulation logic, driven on a fake clock.
    clock, cost, calls = [0.0], [0.0, 0], []
    totals, state = {}, {"stage": None, "started": 0.0, "cost_at_start": 0.0,
                         "calls_at_start": 0}

    def close(now):
        cur = state["stage"]
        if cur is None:
            return None
        slot = totals.setdefault(cur, {"stage": cur, "duration_s": 0.0,
                                       "cost_usd": 0.0, "llm_calls": 0, "segments": 0})
        slot["duration_s"] = round(slot["duration_s"] + (now - state["started"]), 2)
        slot["cost_usd"] = round(slot["cost_usd"] + (cost[0] - state["cost_at_start"]), 6)
        slot["llm_calls"] += cost[1] - state["calls_at_start"]
        slot["segments"] += 1
        return dict(slot)

    for stage, secs, spend, n in sequence:
        if stage != state["stage"]:
            closed = close(clock[0])
            state.update(stage=stage, started=clock[0], cost_at_start=cost[0],
                         calls_at_start=cost[1])
            if closed:
                calls.append(closed)
        clock[0] += secs
        cost[0] = round(cost[0] + spend, 6)
        cost[1] += n
    close(clock[0])
    return totals, clock[0], cost, calls


def test_stage_time_accumulates_across_loop_re_entry():
    """Stage 3 re-opens once per requirement. Keeping only the last segment
    would report the final sliver as the stage's whole cost."""
    seq = [(1, 10, 0.20, 2), (2, 5, 0.10, 1)]
    for _ in range(4):                       # four requirements
        seq += [(3, 8, 0.50, 3), (2, 2, 0.05, 1)]
    totals, elapsed, cost, _ = _run_stage_sequence(seq)

    assert totals[3]["segments"] == 4
    assert totals[3]["duration_s"] == 32.0   # 4 x 8, not 8
    assert totals[3]["cost_usd"] == 2.0      # 4 x 0.50, not 0.50
    assert totals[3]["llm_calls"] == 12      # 4 x 3, not 3


def test_stage_totals_reconcile_with_the_run_total():
    seq = [(1, 10, 0.20, 2), (2, 5, 0.10, 1)]
    for _ in range(3):
        seq += [(3, 8, 0.50, 3), (2, 2, 0.05, 1)]
    totals, elapsed, cost, _ = _run_stage_sequence(seq)
    assert round(sum(s["duration_s"] for s in totals.values()), 2) == round(elapsed, 2)
    assert round(sum(s["cost_usd"] for s in totals.values()), 6) == cost[0]
    assert sum(s["llm_calls"] for s in totals.values()) == cost[1]


def test_stepper_sees_a_monotonically_rising_total():
    """Each re-entry reports the stage's cumulative figure, so the stepper never
    appears to go backwards."""
    seq = [(1, 5, 0.1, 1)]
    for _ in range(3):
        seq += [(3, 8, 0.5, 2), (2, 2, 0.05, 1)]
    _, _, _, emitted = _run_stage_sequence(seq)
    stage3 = [e["cost_usd"] for e in emitted if e["stage"] == 3]
    assert stage3 == sorted(stage3), stage3
    assert len(stage3) >= 2
