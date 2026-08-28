"""
Operation Cost Tracker
=====================
Appends cost/token usage per LLM operation to a JSONL file for auditing and budgeting.
"""

import json
import os
import time
from contextlib import contextmanager
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


def get_model_from_langchain_result(result: Any) -> Optional[str]:
    """The model that actually served the call.

    LangChain reports it in response_metadata; without reading it here every
    record carries model=None, which is what makes per-model cost impossible.
    Reading it in one place fixes all call sites at once.
    """
    if result is None:
        return None
    meta = getattr(result, "response_metadata", None)
    if isinstance(meta, dict):
        for key in ("model_name", "model", "model_id"):
            value = meta.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    value = getattr(result, "model", None)
    return value.strip() if isinstance(value, str) and value.strip() else None


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


# Per-1M-token rates, matched against the resolved model id by substring so
# provider prefixes and version suffixes both hit. Locally hosted models are
# free: billing an Ollama run at GPT-4o rates — which one global rate pair does —
# invents spend that never happened.
_DEFAULT_RATES = {
    "gpt-4o-mini":     (0.15, 0.60),
    "gpt-4o":          (2.50, 10.00),
    "gpt-4.1-mini":    (0.40, 1.60),
    "gpt-4.1":         (2.00, 8.00),
    "gpt-4-turbo":     (10.00, 30.00),
    "gpt-4":           (30.00, 60.00),
    "gpt-3.5-turbo":   (0.50, 1.50),
    "gemini-1.5-flash": (0.075, 0.30),
    "gemini-1.5-pro":  (1.25, 5.00),
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro":  (1.25, 10.00),
    # Hosted embedding models bill on input only.
    "text-embedding-3-small": (0.02, 0.0),
    "text-embedding-3-large": (0.13, 0.0),
    "text-embedding-ada-002": (0.10, 0.0),
    # Locally hosted chat models — no per-token cost.
    "llama": (0.0, 0.0), "mistral": (0.0, 0.0), "qwen": (0.0, 0.0),
    "phi": (0.0, 0.0), "gemma": (0.0, 0.0), "deepseek": (0.0, 0.0),
    # Locally hosted embedding models. These run on the machine and cost
    # nothing; without them the fallback rate invents spend that never happened
    # — observed live, billing all-MiniLM-L6-v2 at GPT-4o rates.
    "sentence-transformers": (0.0, 0.0), "all-minilm": (0.0, 0.0),
    "all-mpnet": (0.0, 0.0), "bge-": (0.0, 0.0), "gte-": (0.0, 0.0),
    "e5-": (0.0, 0.0), "nomic-embed": (0.0, 0.0),
}


def _rate_overrides() -> Dict[str, Any]:
    raw = os.getenv("LLM_COST_RATES_JSON", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def _fallback_rates() -> tuple:
    """The single global pair, kept as the fallback for an unknown model."""
    try:
        return (float(os.getenv("LLM_COST_INPUT_PER_1M", "2.5")),
                float(os.getenv("LLM_COST_OUTPUT_PER_1M", "10.0")))
    except (TypeError, ValueError):
        return (2.5, 10.0)


def rates_for_model(model: Optional[str]) -> tuple:
    """(input_per_1m, output_per_1m) for a model id.

    Longest match wins so "gpt-4o-mini" is not priced as "gpt-4o".
    """
    if not model:
        return _fallback_rates()
    key = str(model).strip().lower()
    table = dict(_DEFAULT_RATES)
    for name, pair in _rate_overrides().items():
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            table[str(name).lower()] = (float(pair[0]), float(pair[1]))
    best = None
    for name, pair in table.items():
        if name in key and (best is None or len(name) > len(best[0])):
            best = (name, pair)
    return best[1] if best else _fallback_rates()


def _estimate_cost_usd(
    input_tokens: Optional[int],
    output_tokens: Optional[int],
    input_per_1m: Optional[float] = None,
    output_per_1m: Optional[float] = None,
    model: Optional[str] = None,
) -> Optional[float]:
    """Estimate cost in USD from token counts using per-1M rates."""
    try:
        default_in, default_out = rates_for_model(model)
        in_per = input_per_1m if input_per_1m is not None else default_in
        out_per = output_per_1m if output_per_1m is not None else default_out
        inp = (input_tokens or 0) / 1_000_000.0 * in_per
        out = (output_tokens or 0) / 1_000_000.0 * out_per
        # 10dp, not 6: at per-1M rates a single cheap call costs ~1e-5, which
        # 6dp quantizes (1.35e-05 -> 1.3e-05). Across thousands of calls that
        # rounding drifts the total. Historical rows keep their 6dp values.
        return round(inp + out, 10)
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
    duration_s: Optional[float] = None,
    stage: Optional[str] = None,
) -> None:
    """
    Append one operation cost record to the operation_costs file (JSONL).
    Safe to call from multiple threads (append-only). If file path is not writable, logs and skips.
    """
    if estimated_cost_usd is None and (input_tokens is not None or output_tokens is not None):
        estimated_cost_usd = _estimate_cost_usd(input_tokens, output_tokens, model=model)
    rate_in, rate_out = rates_for_model(model)
    record = {
        "operation": operation,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_cost_usd": estimated_cost_usd,
        "model": model,
        # Stored per record so editing the rate card later cannot silently
        # rewrite the meaning of spend already on disk.
        "rate_in_per_1m": rate_in,
        "rate_out_per_1m": rate_out,
    }
    if duration_s is not None:
        record["duration_s"] = round(float(duration_s), 3)
    if stage:
        record["stage"] = stage
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
    duration_s: Optional[float] = None,
    stage: Optional[str] = None,
) -> Optional[float]:
    """
    Extract usage from a LangChain invoke result, record one operation, and return estimated cost in USD (or None).
    """
    usage = get_usage_from_langchain_result(result)
    # Prefer what the response says actually served the call over what the
    # caller believed it asked for.
    resolved = get_model_from_langchain_result(result) or model
    cost = _estimate_cost_usd(usage.get("input_tokens"), usage.get("output_tokens"),
                              model=resolved)
    record_operation(
        operation=operation,
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        estimated_cost_usd=cost,
        model=resolved,
        extra=extra,
        run_id=run_id,
        duration_s=duration_s,
        stage=stage,
    )
    return cost


def estimate_embedding_tokens(texts) -> int:
    """Rough token count for embedding input.

    Deliberately an approximation (~4 chars/token): the embedding APIs return
    no usage metadata, and pulling in a tokenizer to price a $0.02/1M call is
    not worth the dependency. The estimate is labelled as such wherever shown.
    """
    try:
        if isinstance(texts, str):
            texts = [texts]
        return max(0, sum(len(t) for t in texts if isinstance(t, str)) // 4)
    except (TypeError, ValueError):
        return 0


def record_embedding(operation: str, texts=None, model: Optional[str] = None,
                     duration_s: Optional[float] = None,
                     run_id: Optional[str] = None,
                     extra: Optional[Dict[str, Any]] = None,
                     n_tokens: Optional[int] = None) -> Optional[float]:
    """Record one embedding call.

    Embeddings bill on input only, so output_tokens stays 0 and the rate card
    must supply a 0 output rate for embedding models rather than letting them
    inherit a chat model's.
    """
    try:
        tokens = n_tokens if n_tokens is not None else estimate_embedding_tokens(texts)
        if not tokens:
            return 0.0
        cost = _estimate_cost_usd(tokens, 0, model=model)
        payload = dict(extra or {})
        payload.setdefault("token_estimate", True)
        if isinstance(texts, (list, tuple)):
            payload.setdefault("n_texts", len(texts))
        record_operation(operation=operation, input_tokens=tokens, output_tokens=0,
                         estimated_cost_usd=cost, model=model, extra=payload,
                         run_id=run_id, duration_s=duration_s)
        return cost
    except Exception:
        return None


@contextmanager
def timed_operation(operation: str, run_id: Optional[str] = None,
                    extra: Optional[Dict[str, Any]] = None,
                    stage: Optional[str] = None):
    """Time a LangChain call and record it, including on failure.

    Usage:
        with timed_operation("rag.query", run_id=rid) as slot:
            slot["result"] = llm.invoke(prompt)

    A call that raises still consumed wall time, and recording it is what stops
    a failing operation looking free.
    """
    slot: Dict[str, Any] = {"result": None}
    started = time.perf_counter()
    try:
        yield slot
    finally:
        elapsed = time.perf_counter() - started
        try:
            if slot.get("result") is not None:
                record_from_langchain_result(operation, slot["result"],
                                             extra=extra, run_id=run_id,
                                             duration_s=elapsed, stage=stage)
            else:
                record_operation(operation, extra=extra, run_id=run_id,
                                 duration_s=elapsed, stage=stage)
        except Exception:
            pass
