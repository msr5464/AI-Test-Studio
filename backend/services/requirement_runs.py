"""
Requirements -> Tests runs
==========================
A run outlives the request that started it, so a refresh or a closed tab no longer
loses it: the same run model the agent pages get from the agent server.

Every event a run emits is kept twice: in memory while the run is live (streams
tail it) and as a line in <sid>.events.jsonl (History replays it). <sid>.json is
the row the History table shows. Replaying the recorded events through the page's
live handler rebuilds a past run exactly, so there is one renderer, not two.

The live registry is in-process. That holds because gunicorn runs a single worker
(gunicorn_config.py); a run whose process died reads as "interrupted".
"""

import json
import os
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

_SID_RE = re.compile(r"\d{8}-\d{6}-req-[0-9a-f]{6}")
_KEEP_PER_USER = 50      # History lists 20; anything past this is unreachable anyway
_DEADLINE_S = 1200       # hard stop, as before, but no longer tied to a connection
_KEEPALIVE_S = 25
_INTERRUPTED = "Interrupted: the server restarted before this run finished."

# Max lengths for streamed results, to avoid huge SSE payloads that fail to send/parse (bigger docs).
# Test case content includes preconditions, steps, expected result, so keep enough to show the full case.
_TRIM_DESC = 6000
_TRIM_CONTENT = 12000
_TRIM_SPEC_CONTENT = 3000


def _trim_result_for_stream(result):
    """Return a copy of the result with long text fields truncated so the SSE payload stays manageable."""
    if not result or not isinstance(result, dict):
        return result
    out = dict(result)
    # Requirements: limit title/description
    if "requirements" in out and isinstance(out["requirements"], list):
        out["requirements"] = [
            {
                **r,
                "title": (r.get("title") or "")[:_TRIM_DESC],
                "description": (r.get("description") or "")[:_TRIM_DESC],
            }
            for r in out["requirements"]
        ]
    # Related specs: limit content
    if "related_specs" in out and isinstance(out["related_specs"], list):
        out["related_specs"] = [
            {**s, "content": ((s.get("content") or "")[:_TRIM_SPEC_CONTENT])}
            for s in out["related_specs"]
        ]
    # Related tests / tests_needing_update: limit content; keep preconditions, steps, expected_result separate (trim if present)
    for key in ("related_tests", "tests_needing_update"):
        if key not in out or not isinstance(out[key], dict):
            continue
        trimmed = {}
        for req_id, lst in out[key].items():
            if not isinstance(lst, list):
                trimmed[req_id] = lst
                continue
            trimmed[req_id] = []
            for t in lst:
                row = {**t, "content": ((t.get("content") or "")[:_TRIM_CONTENT])}
                if "preconditions" in t:
                    row["preconditions"] = (t.get("preconditions") or "")[:_TRIM_CONTENT]
                if "steps" in t:
                    row["steps"] = (t.get("steps") or "")[:_TRIM_CONTENT]
                if "expected_result" in t:
                    row["expected_result"] = (t.get("expected_result") or "")[:_TRIM_CONTENT]
                trimmed[req_id].append(row)
        out[key] = trimmed
    # Generated tests: limit steps, preconditions, expected_result
    if "generated_tests" in out and isinstance(out["generated_tests"], dict):
        trimmed_gt = {}
        for req_id, lst in out["generated_tests"].items():
            if not isinstance(lst, list):
                trimmed_gt[req_id] = lst
                continue
            trimmed_gt[req_id] = [
                {
                    **t,
                    "preconditions": ((t.get("preconditions") or "")[:_TRIM_CONTENT]),
                    "steps": ((t.get("steps") or "")[:_TRIM_CONTENT]),
                    "expected_result": ((t.get("expected_result") or "")[:_TRIM_CONTENT]),
                }
                for t in lst
            ]
        out["generated_tests"] = trimmed_gt
    return out


def _trim_requirement_result_for_stream(data):
    """Trim one requirement's result payload for streaming (keep size small)."""
    if not data or not isinstance(data, dict):
        return data
    out = dict(data)
    if "requirement" in out and isinstance(out["requirement"], dict):
        r = out["requirement"]
        out["requirement"] = {
            **r,
            "title": (r.get("title") or "")[:_TRIM_DESC],
            "description": (r.get("description") or "")[:_TRIM_DESC],
        }
    if "related_specs" in out and isinstance(out["related_specs"], list):
        out["related_specs"] = [
            {**s, "content": ((s.get("content") or "")[:_TRIM_SPEC_CONTENT])}
            for s in out["related_specs"]
        ]
    for key in ("related_tests", "tests_needing_update"):
        if key in out and isinstance(out[key], list):
            original_list = out[key]
            out[key] = []
            for t in original_list:
                row = {**t, "content": ((t.get("content") or "")[:_TRIM_CONTENT])}
                if "preconditions" in t:
                    row["preconditions"] = (t.get("preconditions") or "")[:_TRIM_CONTENT]
                if "steps" in t:
                    row["steps"] = (t.get("steps") or "")[:_TRIM_CONTENT]
                if "expected_result" in t:
                    row["expected_result"] = (t.get("expected_result") or "")[:_TRIM_CONTENT]
                out[key].append(row)
    if "generated_tests" in out and isinstance(out["generated_tests"], list):
        out["generated_tests"] = [
            {
                **t,
                "preconditions": ((t.get("preconditions") or "")[:_TRIM_CONTENT]),
                "steps": ((t.get("steps") or "")[:_TRIM_CONTENT]),
                "expected_result": ((t.get("expected_result") or "")[:_TRIM_CONTENT]),
            }
            for t in out["generated_tests"]
        ]
    return out


# ── Storage ───────────────────────────────────────────────────────────────────

def _dir() -> Path:
    override = (os.getenv("REQUIREMENT_SESSIONS_DIR") or "").strip()
    return Path(override) if override else _PROJECT_ROOT / "storage" / "requirement_sessions"


def _meta_file(sid: str) -> Path:
    return _dir() / f"{sid}.json"


def _events_file(sid: str) -> Path:
    return _dir() / f"{sid}.events.jsonl"


def _write_meta(meta: Dict[str, Any]) -> None:
    path = _meta_file(meta["session_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _read_meta(path: Path) -> Optional[Dict[str, Any]]:
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return meta if isinstance(meta, dict) else None


def _resolved(meta: Dict[str, Any]) -> Dict[str, Any]:
    # A run is only running if this process is running it.
    if meta.get("status") == "running" and meta.get("session_id") not in _LIVE:
        return {**meta, "status": "interrupted"}
    return meta


# ── Live runs ─────────────────────────────────────────────────────────────────

class _Run:
    def __init__(self, meta: Dict[str, Any]):
        self.meta = meta
        self.events: List[str] = []
        self.done = False
        self.timed_out = False
        self.cancel = threading.Event()
        self.cond = threading.Condition()

    def emit(self, event: Dict[str, Any]) -> bool:
        if "ts" not in event:
            event = {**event, "ts": round(time.time(), 3)}    # the console prints [HH:MM:SS]
        try:
            line = json.dumps(event, default=str)
        except (TypeError, ValueError):
            return False
        # Requirements are analysed in parallel, so callbacks arrive from several
        # threads at once; the lock keeps memory and file in the same order.
        with self.cond:
            self.events.append(line)
            try:
                with _events_file(self.meta["session_id"]).open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except OSError:
                pass    # the live view still has it; only History loses the line
            self.cond.notify_all()
        return True


_LIVE: Dict[str, _Run] = {}


def _pct_env(name: str, default: float) -> float:
    try:
        raw = os.getenv(name, "").strip()
        return max(0.0, min(100.0, float(raw))) if raw else default
    except ValueError:
        return default


def _unlink_all(paths) -> None:
    for path in paths:
        try:
            Path(path).unlink()
        except OSError:
            pass


def start(svc, *, user_id: str, text: Optional[str], file_paths: List[Path],
          confluence_urls: List[str], opts: Dict[str, Any], source: str) -> Dict[str, Any]:
    """Register a run, start analysing in the background, and return its History row."""
    now = datetime.now()
    sid = now.strftime("%Y%m%d-%H%M%S") + "-req-" + uuid.uuid4().hex[:6]
    meta = {
        "session_id": sid,
        "user_id": user_id,
        "started_at": now.isoformat(timespec="seconds"),
        "started_epoch": time.time(),
        "status": "running",
        "source": source,
        "title": "",
        "source_type": "confluence" if confluence_urls else "file" if file_paths else "text",
        "generate_new_tests": bool(opts.get("generate_new_tests", True)),
        "duration_s": None, "cost_usd": None, "input_tokens": None, "output_tokens": None,
        "llm_calls": None, "requirements": None, "tests_generated": None, "error": "",
    }
    run = _Run(meta)
    _LIVE[sid] = run
    try:
        _write_meta(meta)
    except OSError:
        _LIVE.pop(sid, None)
        _unlink_all(file_paths)
        raise
    _prune(user_id)
    # First event, so thresholds are known before per-requirement blocks render.
    run.emit({
        "type": "config",
        "session_id": sid,
        "source": source,
        "generate_p2_p3_tests": bool(opts.get("generate_p2_p3_tests")),
        "coverage_min_similarity": _pct_env("REQUIREMENT_TESTS_COVERAGE_MIN_SIMILARITY", 60.0),
        "retrieval_similarity_threshold": _pct_env("REQUIREMENT_TESTS_SIMILARITY_THRESHOLD", 45.0),
        "generate_new_tests": meta["generate_new_tests"],
    })
    threading.Thread(target=_work, args=(run, svc, text, file_paths, confluence_urls, opts),
                     name=f"req-run-{sid}").start()
    return dict(meta)


def _work(run: _Run, svc, text, file_paths, confluence_urls, opts) -> None:
    meta = run.meta
    result, status, error = None, "failed", ""

    def on_deadline():
        run.timed_out = True
        run.cancel.set()

    deadline = threading.Timer(_DEADLINE_S, on_deadline)
    deadline.daemon = True
    deadline.start()

    def progress_cb(stage, message, progress, closed_stage=None):
        # closed_stage carries the duration and cost of the stage that
        # just finished, so the UI can fill in its per-stage timings.
        event = {"stage": stage, "message": message, "progress": progress}
        if closed_stage:
            event["closed_stage"] = closed_stage
        run.emit(event)

    def doc_summary_cb(summary):
        meta["title"] = (summary or {}).get("title") or ""
        run.emit({"type": "doc_summary", "data": summary})

    try:
        result = svc.analyze(
            text=text,
            file_path=file_paths[0] if len(file_paths) == 1 else None,
            file_paths=file_paths if len(file_paths) > 1 else None,
            confluence_url=confluence_urls[0] if len(confluence_urls) == 1 else None,
            confluence_urls=confluence_urls if len(confluence_urls) > 1 else None,
            progress_callback=progress_cb,
            requirement_result_callback=lambda req_id, data: run.emit({
                "type": "requirement_result", "req_id": req_id,
                "data": _trim_requirement_result_for_stream(data)}),
            doc_summary_callback=doc_summary_cb,
            requirement_step_callback=lambda req_id, step: run.emit({
                "type": "requirement_step", "req_id": req_id, "step": step}),
            cancel_event=run.cancel,
            log_callback=run.emit,
            **opts,
        )
        # A cancelled or timed-out run still returns what it analysed so far.
        if run.timed_out:
            status, error = "failed", "Stopped after 20 minutes."
        else:
            status = "cancelled" if run.cancel.is_set() else "completed"
        final = {**_trim_result_for_stream(result), "status": status}
        if error:
            final["error"] = error
        if not run.emit(final):
            status, error = "failed", "Failed to serialize the result."
            run.emit({"success": False, "error": error, "status": status})
    except Exception as exc:
        status = "cancelled" if run.cancel.is_set() and not run.timed_out else "failed"
        error = str(exc)
        run.emit({"success": False, "error": error, "status": status})
    finally:
        deadline.cancel()
        # Every terminal path lands here. A failed run still spent money.
        try:
            from backend.services.analytics_service import record_requirement_run
            record_requirement_run(result, status=status, source_type=meta["source_type"],
                                   started_at=meta["started_epoch"], error=error,
                                   user_id=meta["user_id"])
        except Exception:
            pass
        _finish(run, result, status, error)
        _unlink_all(file_paths)


def _finish(run: _Run, result, status: str, error: str) -> None:
    meta = run.meta
    res = result if isinstance(result, dict) else {}
    summary = res.get("summary") or {}
    meta.update(
        status=status,
        error=(error or "")[:500],
        duration_s=round(time.time() - meta["started_epoch"], 2),
        cost_usd=res.get("total_estimated_cost_usd"),
        input_tokens=res.get("input_tokens"),
        output_tokens=res.get("output_tokens"),
        llm_calls=res.get("llm_calls"),
        requirements=res.get("requirements_analyzed"),
        tests_generated=((summary.get("total_generated_tests") or 0)
                         + (summary.get("e2e_workflow_tests_count") or 0)) if summary else None,
    )
    try:
        _write_meta(meta)
    except OSError:
        pass    # the run is over either way; History will read it as interrupted
    with run.cond:
        run.done = True
        run.cond.notify_all()
    _LIVE.pop(meta["session_id"], None)


def _prune(user_id: str) -> None:
    for meta in list_runs(user_id, limit=None)[_KEEP_PER_USER:]:
        sid = meta.get("session_id") or ""
        if _SID_RE.fullmatch(sid) and sid not in _LIVE:
            _unlink_all((_meta_file(sid), _events_file(sid)))


# ── Reading ───────────────────────────────────────────────────────────────────

def list_runs(user_id: str, limit: Optional[int] = 20) -> List[Dict[str, Any]]:
    """This user's runs, newest first (session ids sort by start time)."""
    rows = []
    for path in _dir().glob("*.json"):
        meta = _read_meta(path)
        if meta and meta.get("user_id") == user_id:
            rows.append(_resolved(meta))
    rows.sort(key=lambda m: m.get("session_id") or "", reverse=True)
    return rows[:limit]


def get_meta(sid: str) -> Optional[Dict[str, Any]]:
    if not _SID_RE.fullmatch(sid or ""):
        return None
    meta = _read_meta(_meta_file(sid))
    return _resolved(meta) if meta else None


def _lines(sid: str) -> List[str]:
    run = _LIVE.get(sid)
    if run is not None:
        with run.cond:
            return list(run.events)
    meta = get_meta(sid)
    if meta is None:
        return []
    try:
        lines = [line for line in _events_file(sid).read_text(encoding="utf-8").splitlines()
                 if line.strip()]
    except OSError:
        lines = []
    if meta.get("status") == "interrupted":
        lines.append(json.dumps({"success": False, "status": "interrupted", "error": _INTERRUPTED}))
    return lines


def events(sid: str) -> List[Dict[str, Any]]:
    """Every recorded event of a run, for replay in one request."""
    out = []
    for line in _lines(sid):
        try:
            out.append(json.loads(line))
        except ValueError:
            continue    # a truncated final line is the expected damage
    return out


def stream(sid: str, offset: int = 0) -> Iterator[str]:
    """SSE frames from `offset`: a live run is tailed until it ends, a finished one is sent and closed.

    Each frame carries `id:`, so EventSource's own reconnect sends Last-Event-ID
    and resumes exactly where it dropped.
    """
    run = _LIVE.get(sid)
    if run is None:
        for i, line in enumerate(_lines(sid)):
            if i >= offset:
                yield f"id: {i}\ndata: {line}\n\n"
        return
    i = offset
    while True:
        with run.cond:
            if i >= len(run.events) and not run.done:
                run.cond.wait(timeout=_KEEPALIVE_S)
            batch, done = run.events[i:], run.done
        if not batch and not done:
            yield ": keepalive\n\n"
            continue
        for line in batch:
            yield f"id: {i}\ndata: {line}\n\n"
            i += 1
        if done:
            return


def cancel(sid: str) -> bool:
    run = _LIVE.get(sid)
    if run is None:
        return False
    run.cancel.set()
    return True


# ── TestRail pushes ───────────────────────────────────────────────────────────

_PUSH_LOCK = threading.Lock()


def _push_target(raw: Any) -> Optional[Dict[str, Any]]:
    """The test on the results page a push came from, reduced to what the page finds it by."""
    if not isinstance(raw, dict):
        return None
    kind = raw.get("kind")
    try:
        if kind == "generated":
            return {"kind": kind, "req_id": str(raw.get("req_id") or "")[:100], "index": int(raw["index"])}
        if kind == "e2e":
            return {"kind": kind, "index": int(raw["index"])}
        if kind == "existing":
            return {"kind": kind, "testrail_id": str(raw.get("testrail_id") or "")[:40]}
    except (KeyError, TypeError, ValueError):
        return None
    return None


def record_push(sid: str, user_id: str, action: str, testrail_id: str, title: str, target: Any) -> bool:
    """Record a successful TestRail push on the run it came from.

    It lands at the end of the run's event log, so reopening the run replays it onto
    results already on the page: the test shows as pushed and cannot be pushed twice
    by mistake.
    """
    meta = get_meta(sid)
    target = _push_target(target)
    if not meta or meta.get("user_id") != user_id or target is None or not testrail_id:
        return False
    event = {"type": "testrail_push", "action": action, "testrail_id": str(testrail_id)[:40],
             "title": str(title or "")[:300], "target": target}
    run = _LIVE.get(sid)
    if run is not None:
        return run.emit(event)
    with _PUSH_LOCK:
        try:
            with _events_file(sid).open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({**event, "ts": round(time.time(), 3)}) + "\n")
        except OSError:
            return False
    return True
