"""Read-side aggregation over the Studio's LLM cost records.

Two sources, deliberately kept apart because they are not the same kind of number:

  storage/operation_costs.jsonl   one row per LLM call (written since 2026-02-22,
                                  4,843 rows before this module existed and never
                                  read back until now)
  storage/requirement_runs.jsonl  one row per completed Requirements->Tests run

Cost here is ESTIMATED from a token rate card. The QA agents' cost is reported by
the Claude CLI itself. Never present a single unlabelled total spanning both.
"""

import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

WINDOWS = {"24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400, "all": None}

# operation -> the pipeline stage it belongs to. The operation name IS the stage
# dimension for this flow; this only groups them for display.
_OPERATION_STAGE = {
    "requirement_analysis.extract_acs": 1,
    "requirement_analysis.assess_all_tests_batch": 2,
    "requirement_analysis.assess_updates": 2,
    "requirement_analysis.coverage_sufficient": 2,
    "requirement_analysis.e2e_coverage_sufficient": 2,
    "requirement_analysis.generate_tests": 3,
    "requirement_analysis.identify_e2e_workflows": 3,
    "requirement_analysis.generate_e2e_test": 3,
    "requirement_analysis.generate_e2e_tests_batch": 3,
}

_INGEST_PREFIX = "ingest."
_RAG_PREFIX = "rag."


def _costs_file() -> Path:
    override = (os.getenv("OPERATION_COSTS_FILE") or "").strip()
    return Path(override) if override else _PROJECT_ROOT / "storage" / "operation_costs.jsonl"


def _runs_file() -> Path:
    override = (os.getenv("REQUIREMENT_RUNS_FILE") or "").strip()
    return Path(override) if override else _PROJECT_ROOT / "storage" / "requirement_runs.jsonl"


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except (ValueError, TypeError):
                    continue      # a truncated final line is the expected damage
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        return []
    return rows


def _epoch(ts: Any) -> float:
    if not isinstance(ts, str) or not ts:
        return 0.0
    try:
        text = ts.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (ValueError, TypeError):
        return 0.0


def _group(operation: str) -> str:
    if operation.startswith(_INGEST_PREFIX):
        return "ingestion"
    if operation.startswith(_RAG_PREFIX):
        return "ask"
    return "requirements"


# ── Writing ───────────────────────────────────────────────────────────────────

def _run_token_totals(run_id: str) -> Dict[str, Any]:
    """input/output token totals and the per-model split for one run."""
    totals = {"input_tokens": 0, "output_tokens": 0, "by_model": {}}
    if not run_id:
        return totals
    for row in _read_jsonl(_costs_file()):
        if row.get("run_id") != run_id:
            continue
        inp = int(row.get("input_tokens") or 0)
        out = int(row.get("output_tokens") or 0)
        totals["input_tokens"] += inp
        totals["output_tokens"] += out
        model = str(row.get("model") or "unknown")
        slot = totals["by_model"].setdefault(
            model, {"cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "calls": 0})
        slot["cost_usd"] = round(
            slot["cost_usd"] + float(row.get("estimated_cost_usd") or 0.0), 10)
        slot["input_tokens"] += inp
        slot["output_tokens"] += out
        slot["calls"] += 1
    return totals


def record_requirement_run(result: Optional[Dict[str, Any]], *,
                           run_id: str = "", status: str = "completed",
                           source_type: str = "", started_at: Optional[float] = None,
                           error: str = "") -> bool:
    """Append one Requirements->Tests run summary. Best-effort.

    Called from the stream route's `finally`, so it fires on every terminal path
    — success, exception, the 20-minute deadline, and client disconnect. A run
    that failed still spent money and must still be recorded.
    """
    try:
        result = result if isinstance(result, dict) else {}
        summary = result.get("summary") or {}
        ended = time.time()
        started = started_at or ended - float(result.get("duration_s") or 0.0)
        record = {
            "schema": 1,
            "run_id": run_id or result.get("run_id") or "",
            "started_at": started,
            "ended_at": ended,
            "duration_s": result.get("duration_s") or round(ended - started, 2),
            "status": status,
            "source_type": source_type,
            "error": error[:500] if error else "",
            "cost_usd": float(result.get("total_estimated_cost_usd") or 0.0),
            "llm_calls": int(result.get("llm_calls") or 0),
            # Tokens and the per-model split come from the operation records
            # this run already wrote, rather than threading two more
            # accumulators through every LLM helper in the service.
            **_run_token_totals(run_id or result.get("run_id") or ""),
            "stages": result.get("stage_timings") or [],
            "outcomes": {
                "requirements_analyzed": int(result.get("requirements_analyzed") or 0),
                "test_cases_generated": int(summary.get("total_generated_tests") or 0),
                "e2e_tests_generated": int(summary.get("e2e_workflow_tests_count") or 0),
                "requirements_covered": int(summary.get("requirements_with_coverage") or 0),
                "uncovered_count": int(summary.get("uncovered_count") or 0),
                "pushed_to_testrail": bool(result.get("pushed_to_testrail")),
            },
            "written_at": ended,
        }
        line = (json.dumps(record, ensure_ascii=False, default=str) + "\n").encode("utf-8")
        path = _runs_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
        return True
    except Exception:
        return False


# ── Reading ───────────────────────────────────────────────────────────────────

def _blank() -> Dict[str, Any]:
    return {"calls": 0, "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0,
            "duration_s": 0.0, "runs": 0}


def _add(target: Dict[str, Any], row: Dict[str, Any]) -> None:
    target["calls"] += 1
    target["cost_usd"] = round(target["cost_usd"] + float(row.get("estimated_cost_usd") or 0.0), 6)
    target["input_tokens"] += int(row.get("input_tokens") or 0)
    target["output_tokens"] += int(row.get("output_tokens") or 0)
    target["duration_s"] = round(target["duration_s"] + float(row.get("duration_s") or 0.0), 3)


def query(window: str = "7d", since: Optional[float] = None,
          until: Optional[float] = None) -> Dict[str, Any]:
    """Rollups over the Studio's own LLM spend."""
    now = time.time()
    if since is None and WINDOWS.get(window) is not None:
        since = now - WINDOWS[window]
    until = until or now

    operations = _read_jsonl(_costs_file())
    runs = _read_jsonl(_runs_file())

    data_since = min((_epoch(r.get("timestamp_utc")) for r in operations
                      if _epoch(r.get("timestamp_utc"))), default=None)

    overall = _blank()
    by_group: Dict[str, Dict[str, Any]] = defaultdict(_blank)
    by_operation: Dict[str, Dict[str, Any]] = defaultdict(_blank)
    by_model: Dict[str, Dict[str, Any]] = defaultdict(_blank)
    by_stage: Dict[int, Dict[str, Any]] = defaultdict(_blank)
    series: Dict[str, Dict[str, Any]] = defaultdict(_blank)
    run_ids = set()
    # 42 of the 4,843 existing rows have no run_id. They still count toward
    # spend; they just cannot be attributed to a run.
    orphan_calls = 0

    for row in operations:
        ts = _epoch(row.get("timestamp_utc"))
        if since is not None and ts < since:
            continue
        if ts > until:
            continue
        operation = str(row.get("operation") or "unknown")
        _add(overall, row)
        _add(by_group[_group(operation)], row)
        _add(by_operation[operation], row)
        _add(by_model[str(row.get("model") or "unknown")], row)
        stage = _OPERATION_STAGE.get(operation)
        if stage:
            _add(by_stage[stage], row)
        _add(series[time.strftime("%Y-%m-%d", time.localtime(ts))], row)
        if row.get("run_id"):
            run_ids.add(row["run_id"])
        else:
            orphan_calls += 1

    selected_runs = [r for r in runs
                     if (since is None or float(r.get("started_at") or 0) >= since)
                     and float(r.get("started_at") or 0) <= until]

    outcomes = defaultdict(int)
    run_duration_total = 0.0
    for run in selected_runs:
        for key, value in (run.get("outcomes") or {}).items():
            if isinstance(value, bool):
                outcomes[key] += 1 if value else 0
            elif isinstance(value, (int, float)):
                outcomes[key] += value
        run_duration_total += float(run.get("duration_s") or 0.0)

    # Runs with no summary row (everything before 5c) still have a usable
    # duration: the span of their own LLM-call timestamps. Without this the 250+
    # runs of existing history contribute no time at all, which was the whole
    # point of reading them. Kept in a separate field so it is never silently
    # added to measured time.
    summarised_ids = {r.get("run_id") for r in selected_runs if r.get("run_id")}
    approx_spans = approximate_run_durations()
    approx_total = round(sum(v for rid, v in approx_spans.items()
                             if rid in run_ids and rid not in summarised_ids), 2)

    overall["runs"] = len(run_ids)
    # Runs predating the summary record have no measured duration. Their LLM
    # timestamps still bound them, which is an approximation, not a measurement.
    approx = len(run_ids) - len(selected_runs)
    return {
        "window": {"from": since, "to": until, "label": _window_label(window)},
        "data_since": data_since,
        "cost_basis": "estimated",
        "overall": overall,
        "runs_summarised": len(selected_runs),
        "runs_duration_approx": max(0, approx),
        "run_duration_s": round(run_duration_total, 2),
        # Approximate: excludes retrieval, parsing, and anything outside the
        # first and last LLM call. Every consumer must label it as such.
        "run_duration_approx_s": approx_total,
        "orphan_calls": orphan_calls,
        "outcomes": dict(outcomes),
        "by_group": dict(by_group),
        "by_operation": dict(by_operation),
        "by_model": dict(by_model),
        "by_stage": {str(k): v for k, v in sorted(by_stage.items())},
        "ingestion": ingestion_history(since, until),
        "series": [dict(bucket=b, **v) for b, v in sorted(series.items())],
    }


def turn_metrics(run_id: Optional[str], since: Optional[float] = None) -> Dict[str, Any]:
    """Cost and token totals for the records a single chat turn just wrote.

    Read back from the cost file rather than threaded through the RAG stack:
    one turn fans out into a retrieval-expansion call plus the answer call, and
    reading the records is simpler than plumbing a return value through both.
    """
    totals = {"cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "calls": 0}
    if not run_id:
        return totals
    for row in _read_jsonl(_costs_file()):
        if row.get("run_id") != run_id:
            continue
        if since is not None and _epoch(row.get("timestamp_utc")) < since - 1:
            continue
        totals["calls"] += 1
        totals["cost_usd"] = round(
            totals["cost_usd"] + float(row.get("estimated_cost_usd") or 0.0), 10)
        totals["input_tokens"] += int(row.get("input_tokens") or 0)
        totals["output_tokens"] += int(row.get("output_tokens") or 0)
    return totals


def ingestion_history(since: Optional[float] = None,
                      until: Optional[float] = None) -> Dict[str, Any]:
    """Sync durations, read from the metadata each sync service already writes.

    Both services persist `duration_seconds` per sync in storage/*_sync_metadata.json,
    so this needs no new write path — only a reader. Embedding COST for those
    syncs comes from the `ingest.*` operation records instead.
    """
    out: Dict[str, Any] = {"syncs": [], "total_duration_s": 0.0, "count": 0}
    for source, filename in (("testrail", "testrail_sync_metadata.json"),
                             ("confluence", "confluence_sync_metadata.json")):
        path = _PROJECT_ROOT / "storage" / filename
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        for entry in (data.get("syncs") or []):
            if not isinstance(entry, dict):
                continue
            ts = _epoch(entry.get("timestamp"))
            if since is not None and ts and ts < since:
                continue
            if until is not None and ts and ts > until:
                continue
            duration = float(entry.get("duration_seconds") or 0.0)
            out["syncs"].append({
                "source": source,
                "timestamp": entry.get("timestamp"),
                "duration_s": round(duration, 2),
                "status": entry.get("status"),
                "items": (entry.get("test_cases_fetched")
                          or entry.get("pages_fetched") or 0),
            })
            out["total_duration_s"] = round(out["total_duration_s"] + duration, 2)
            out["count"] += 1
    out["syncs"].sort(key=lambda s: s.get("timestamp") or "", reverse=True)
    return out


def approximate_run_durations() -> Dict[str, float]:
    """Per-run duration inferred from the span of its LLM-call timestamps.

    This is what makes 247 runs of pre-existing history usable. It excludes
    retrieval and parsing time and anything outside the first and last call, so
    every consumer must label it as approximate.
    """
    spans: Dict[str, List[float]] = defaultdict(list)
    for row in _read_jsonl(_costs_file()):
        rid = row.get("run_id")
        ts = _epoch(row.get("timestamp_utc"))
        if rid and ts:
            spans[rid].append(ts)
    return {rid: round(max(v) - min(v), 2) for rid, v in spans.items()}


def _window_label(window: str) -> str:
    return {"24h": "Last 24 hours", "7d": "Last 7 days",
            "30d": "Last 30 days", "all": "All time"}.get(window, window)
