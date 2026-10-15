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


def _group(operation: str, run_id: Optional[str] = None,
           recorded: frozenset = frozenset()) -> str:
    """The Studio area a call belongs to.

    "requirements" is only the calls of a RECORDED Requirements -> Tests run —
    the runs that section and the QA Agents tab count — so its cost, calls and
    stage bars agree with them. Other requirement calls (case-update
    suggestions, a run that never wrote its summary) still count, apart, as
    "requirements_other". ingest.embed_query embeds a question at retrieval
    time, not a synced document, so it goes with the question.
    """
    if run_id and run_id in recorded:
        return "requirements"
    if operation.startswith(_RAG_PREFIX):
        return "ask"
    if operation == "ingest.embed_query":
        return "ask" if not run_id or str(run_id).startswith("ask-") else "requirements_other"
    if operation.startswith(_INGEST_PREFIX):
        return "ingestion"
    return "requirements_other"


def _local_epoch(ts: Any) -> float:
    """Sync metadata is written with datetime.now().isoformat(): local time, no
    offset. _epoch() reads a naive value as UTC, which moved every sync 5.5h
    (IST) — across window edges and onto the wrong day."""
    try:
        return datetime.fromisoformat(ts).timestamp() if isinstance(ts, str) and ts else 0.0
    except ValueError:
        return 0.0


# ── Writing ───────────────────────────────────────────────────────────────────

def _run_token_totals(run_id: str) -> Dict[str, Any]:
    """input/output token totals and the per-model split for one run."""
    totals = {"input_tokens": 0, "output_tokens": 0, "calls": 0, "cost_usd": 0.0, "by_model": {}}
    if not run_id:
        return totals
    for row in _read_jsonl(_costs_file()):
        if row.get("run_id") != run_id:
            continue
        inp = int(row.get("input_tokens") or 0)
        out = int(row.get("output_tokens") or 0)
        c_usd = float(row.get("estimated_cost_usd") or 0.0)
        totals["input_tokens"] += inp
        totals["output_tokens"] += out
        totals["calls"] += 1
        totals["cost_usd"] = round(totals["cost_usd"] + c_usd, 10)
        model = str(row.get("model") or "unknown")
        slot = totals["by_model"].setdefault(
            model, {"cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "calls": 0})
        slot["cost_usd"] = round(
            slot["cost_usd"] + c_usd, 10)
        slot["input_tokens"] += inp
        slot["output_tokens"] += out
        slot["calls"] += 1
    totals["llm_calls"] = totals["calls"]
    return totals


def record_requirement_run(result: Optional[Dict[str, Any]], *,
                           run_id: str = "", status: str = "completed",
                           source_type: str = "", started_at: Optional[float] = None,
                           error: str = "", user_id: str = "default") -> bool:
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
        resolved_rid = run_id or result.get("run_id") or ""
        tt = _run_token_totals(resolved_rid)
        record = {
            "schema": 1,
            "run_id": resolved_rid,
            "user_id": user_id or "default",
            "started_at": started,
            "ended_at": ended,
            "duration_s": result.get("duration_s") or round(ended - started, 2),
            "status": status,
            "source_type": source_type,
            "error": error[:500] if error else "",
            **tt,   # first, or its zeros overwrite the fallbacks below
            "cost_usd": tt.get("cost_usd") if tt.get("cost_usd") else float(result.get("total_estimated_cost_usd") or 0.0),
            "llm_calls": tt.get("llm_calls") if tt.get("llm_calls") else int(result.get("llm_calls") or 0),
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


def _blank_run_rollup() -> Dict[str, Any]:
    return {"runs": 0, "succeeded": 0, "failed": 0, "cost_usd": 0.0, "duration_s": 0.0,
            "llm_calls": 0, "input_tokens": 0, "output_tokens": 0, "tests_generated": 0}


def _add_run(target: Dict[str, Any], run: Dict[str, Any]) -> None:
    """Runs count only a verdict (completed/failed), as for the QA agents; a
    cancelled run still adds its spend, time and tests."""
    status = run.get("status")
    if status in ("completed", "failed"):
        target["runs"] += 1
        target["succeeded" if status == "completed" else "failed"] += 1
    outcomes = run.get("outcomes") or {}
    target["tests_generated"] += (int(outcomes.get("test_cases_generated") or 0)
                                  + int(outcomes.get("e2e_tests_generated") or 0))
    target["cost_usd"] = round(target["cost_usd"] + float(run.get("cost_usd") or 0.0), 6)
    target["duration_s"] = round(target["duration_s"] + float(run.get("duration_s") or 0.0), 2)
    target["llm_calls"] += int(run.get("llm_calls") or 0)
    target["input_tokens"] += int(run.get("input_tokens") or 0)
    target["output_tokens"] += int(run.get("output_tokens") or 0)


ADMIN_USER_ID = "21232f297a57"


def _resolve_row_user(row: Dict[str, Any]) -> str:
    uid = row.get("user_id")
    if not uid or uid in ("default", "admin"):
        return ADMIN_USER_ID
    return str(uid)


def query(window: str = "7d", since: Optional[float] = None,
          until: Optional[float] = None,
          user_id: Optional[str] = None) -> Dict[str, Any]:
    """Rollups over the Studio's own LLM spend."""
    now = time.time()
    if since is None and WINDOWS.get(window) is not None:
        since = now - WINDOWS[window]
    until = until or now

    operations = _read_jsonl(_costs_file())
    runs = _read_jsonl(_runs_file())
    recorded = frozenset(r.get("run_id") for r in runs if r.get("run_id"))

    data_since = min((_epoch(r.get("timestamp_utc")) for r in operations
                      if _epoch(r.get("timestamp_utc"))), default=None)

    overall = _blank()
    by_group: Dict[str, Dict[str, Any]] = defaultdict(_blank)
    by_operation: Dict[str, Dict[str, Any]] = defaultdict(_blank)
    by_model: Dict[str, Dict[str, Any]] = defaultdict(_blank)
    by_stage: Dict[int, Dict[str, Any]] = defaultdict(_blank)
    series: Dict[str, Dict[str, Any]] = defaultdict(_blank)
    # "Time taken" and the trend's Time are one measure, per day: each
    # Requirements -> Tests run's wall time, each other call's own time, each
    # sync. Summing every call's duration instead put the chart at ~2 min under
    # a 10-min tile, since a run's calls are a fraction of its wall time.
    time_by_day: Dict[str, float] = defaultdict(float)
    questions = set()

    for row in operations:
        ts = _epoch(row.get("timestamp_utc"))
        if since is not None and ts < since:
            continue
        if ts > until:
            continue
        if user_id:
            row_user = _resolve_row_user(row)
            if row_user != user_id:
                continue
        operation = str(row.get("operation") or "unknown")
        _add(overall, row)
        grp = _group(operation, row.get("run_id"), recorded)
        _add(by_group[grp], row)
        _add(by_operation[operation], row)
        _add(by_model[str(row.get("model") or "unknown")], row)
        stage = _OPERATION_STAGE.get(operation)
        if stage and grp == "requirements":
            _add(by_stage[stage], row)
        bucket = time.strftime("%Y-%m-%d", time.localtime(ts))
        _add(series[bucket], row)
        if grp in ("ask", "requirements_other"):
            time_by_day[bucket] += float(row.get("duration_s") or 0.0)
        if operation.startswith(_RAG_PREFIX):
            # One question can take several calls (query expansion + answer),
            # all under the question's run id.
            questions.add(row.get("run_id") or id(row))

    selected_runs = [r for r in runs
                     if (since is None or float(r.get("started_at") or 0) >= since)
                     and float(r.get("started_at") or 0) <= until
                     and (not user_id or _resolve_row_user(r) == user_id)]

    outcomes = defaultdict(int)
    # Requirements->Tests is test-design-agent on the QA Agents tab. Summary rows
    # only: operation rows repeat the same calls, so adding both double counts.
    req = _blank_run_rollup()
    req_series: Dict[str, Dict[str, Any]] = defaultdict(_blank_run_rollup)
    for run in selected_runs:
        for key, value in (run.get("outcomes") or {}).items():
            if isinstance(value, bool):
                outcomes[key] += 1 if value else 0
            elif isinstance(value, (int, float)):
                outcomes[key] += value
        _add_run(req, run)
        bucket = time.strftime("%Y-%m-%d", time.localtime(float(run.get("started_at") or 0)))
        _add_run(req_series[bucket], run)
        time_by_day[bucket] += float(run.get("duration_s") or 0.0)

    # Runs and time come from summary rows only. A call with no summary
    # ("suggest case update", a run that died before writing one) still counts
    # in spend, under requirements_other.
    overall["runs"] = len(selected_runs)
    by_group["requirements"]["runs"] = len(selected_runs)
    by_group["ask"]["questions"] = len(questions)
    ingest = ingestion_history(since, until) if (not user_id or user_id == ADMIN_USER_ID) else {"syncs": [], "total_duration_s": 0.0, "count": 0}
    for sync in ingest["syncs"]:
        ts = _local_epoch(sync.get("timestamp"))
        if ts:   # an unreadable timestamp would open the trend at 1970
            time_by_day[time.strftime("%Y-%m-%d", time.localtime(ts))] += sync["duration_s"]
    for bucket in set(series) | set(time_by_day):
        series[bucket]["duration_s"] = round(time_by_day.get(bucket, 0.0), 3)
    return {
        "window": {"from": since, "to": until, "label": _window_label(window)},
        "data_since": data_since,
        "cost_basis": "estimated",
        "overall": overall,
        "runs_summarised": len(selected_runs),
        "run_duration_s": req["duration_s"],
        # The sum of the per-day values the trend draws, so the tile and the
        # chart's total round the same way (90.499 read 1m 31s over 1m 30s).
        "time_taken_s": round(sum(v["duration_s"] for v in series.values()), 3),
        "run_totals": {k: req[k] for k in
                       ("llm_calls", "cost_usd", "input_tokens", "output_tokens")},
        "requirements": req,
        "requirements_series": [dict(bucket=b, **v) for b, v in sorted(req_series.items())],
        "outcomes": dict(outcomes),
        "by_group": dict(by_group),
        "by_operation": dict(by_operation),
        "by_model": dict(by_model),
        "by_stage": {str(k): v for k, v in sorted(by_stage.items())},
        "ingestion": ingest,
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
            ts = _local_epoch(entry.get("timestamp"))
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


def clear_analytics(user_id: Optional[str] = None, window: str = "all",
                    since: Optional[float] = None, until: Optional[float] = None):
    import time
    now = time.time()
    if since is None and window in WINDOWS and WINDOWS[window] is not None:
        since = now - WINDOWS[window]
    wipe_all = (not user_id or user_id == "all") and since is None and until is None

    def _outside(ts: float) -> bool:
        return (since is not None and ts < since) or (until is not None and ts > until)

    costs_path = _costs_file()
    if costs_path.exists():
        if wipe_all:
            costs_path.write_text("")
        else:
            rows = _read_jsonl(costs_path)
            kept = []
            for r in rows:
                r_uid = _resolve_row_user(r)
                if (not user_id or user_id == "all" or r_uid == user_id):
                    # It's a match on user. Now check time.
                    ts = _epoch(r.get("timestamp_utc"))
                    if _outside(ts):
                        kept.append(r) # outside the window, keep it
                    else:
                        pass # delete it
                else:
                    kept.append(r) # different user, keep it

            with costs_path.open("w", encoding="utf-8") as f:
                for r in kept:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
                    
    runs_path = _runs_file()
    if runs_path.exists():
        if wipe_all:
            runs_path.write_text("")
        else:
            rows = _read_jsonl(runs_path)
            kept = []
            for r in rows:
                r_uid = _resolve_row_user(r)
                if (not user_id or user_id == "all" or r_uid == user_id):
                    # Match on user. Now check time.
                    ts = float(r.get("started_at") or 0)
                    if _outside(ts):
                        kept.append(r) # outside the window, keep
                    else:
                        pass # delete
                else:
                    kept.append(r) # different user, keep

            with runs_path.open("w", encoding="utf-8") as f:
                for r in kept:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _window_label(window: str) -> str:
    return {"24h": "Last 24 hours", "7d": "Last 7 days",
            "30d": "Last 30 days", "all": "All time"}.get(window, window)
