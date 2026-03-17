"""
Operation Cost Tracker
=====================
Appends cost/token usage per LLM operation to a JSONL file for auditing and budgeting.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# Project root: backend/cost_tracker.py -> backend -> project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _costs_file_path() -> Path:
    path = os.getenv("OPERATION_COSTS_FILE", "").strip()
    if path:
        return Path(path).resolve()
    storage_dir = _PROJECT_ROOT / "storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    return storage_dir / "operation_costs.jsonl"


def get_usage_from_langchain_result(result: Any) -> Dict[str, int]:
    """
    Extract input/output token counts from a LangChain LLM invoke result (AIMessage).
    Supports response_metadata and usage_metadata.
    """
    out = {"input_tokens": None, "output_tokens": None}
    if result is None:
        return out
    # usage_metadata (LangChain standard)
    if hasattr(result, "usage_metadata") and result.usage_metadata:
        um = result.usage_metadata
        if isinstance(um, dict):
            out["input_tokens"] = um.get("input_tokens")
            out["output_tokens"] = um.get("output_tokens")
        else:
            out["input_tokens"] = getattr(um, "input_tokens", None)
            out["output_tokens"] = getattr(um, "output_tokens", None)
    # response_metadata (e.g. OpenAI)
    if (out["input_tokens"] is None or out["output_tokens"] is None) and hasattr(
        result, "response_metadata"
    ):
        rm = result.response_metadata or {}
        usage = rm.get("usage_metadata") or rm.get("usage") or {}
        if isinstance(usage, dict):
            out["input_tokens"] = out["input_tokens"] or usage.get("input_tokens") or usage.get("prompt_tokens")
            out["output_tokens"] = out["output_tokens"] or usage.get("output_tokens") or usage.get("completion_tokens")
    return out


def _estimate_cost_usd(
    input_tokens: Optional[int],
    output_tokens: Optional[int],
    input_per_1m: Optional[float] = None,
    output_per_1m: Optional[float] = None,
) -> Optional[float]:
    """Estimate cost in USD from token counts using per-1M rates (defaults approximate GPT-4o)."""
    try:
        in_per = input_per_1m if input_per_1m is not None else float(os.getenv("LLM_COST_INPUT_PER_1M", "2.5"))
        out_per = output_per_1m if output_per_1m is not None else float(os.getenv("LLM_COST_OUTPUT_PER_1M", "10.0"))
        inp = (input_tokens or 0) / 1_000_000.0 * in_per
        out = (output_tokens or 0) / 1_000_000.0 * out_per
        return round(inp + out, 6)
    except (TypeError, ValueError):
        return None


def estimate_cost_from_langchain_result(result: Any) -> Optional[float]:
    """Get token usage from a LangChain result and return estimated cost in USD (or None)."""
    usage = get_usage_from_langchain_result(result)
    return _estimate_cost_usd(usage.get("input_tokens"), usage.get("output_tokens"))


def record_operation(
    operation: str,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    estimated_cost_usd: Optional[float] = None,
    model: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
    run_id: Optional[str] = None,
) -> None:
    """
    Append one operation cost record to the operation_costs file (JSONL).
    Safe to call from multiple threads (append-only). If file path is not writable, logs and skips.
    """
    if estimated_cost_usd is None and (input_tokens is not None or output_tokens is not None):
        estimated_cost_usd = _estimate_cost_usd(input_tokens, output_tokens)
    record = {
        "operation": operation,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_cost_usd": estimated_cost_usd,
        "model": model,
    }
    if run_id:
        record["run_id"] = run_id
    if extra:
        record["extra"] = extra
    try:
        path = _costs_file_path()
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"⚠️  Cost tracker could not write to {_costs_file_path()}: {e}")


def record_from_langchain_result(
    operation: str,
    result: Any,
    model: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
    run_id: Optional[str] = None,
) -> Optional[float]:
    """
    Extract usage from a LangChain invoke result, record one operation, and return estimated cost in USD (or None).
    """
    usage = get_usage_from_langchain_result(result)
    cost = _estimate_cost_usd(usage.get("input_tokens"), usage.get("output_tokens"))
    record_operation(
        operation=operation,
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        estimated_cost_usd=cost,
        model=model,
        extra=extra,
        run_id=run_id,
    )
    return cost
