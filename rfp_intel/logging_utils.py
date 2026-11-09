"""Observability: StepLog JSONL trace writer and the @log_step node decorator.

Every graph node is wrapped in @log_step. While a node runs, a per-node
recorder collects tool calls (search queries, LLM calls) and token usage;
`llm.py` and the retrieval node report into it via `record_tool_call` and
`record_llm_call`. After the node returns (or fails) one StepLog line is
appended to logs/trace_<run_id>.jsonl and added to the state's `usage` list.
"""

from __future__ import annotations

import functools
import threading
import time
import uuid
from collections import defaultdict
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel

from rfp_intel.config import settings
from rfp_intel.schemas import RunSummary, StepLog, Usage

_SUMMARY_LIMIT = 500

_lock = threading.Lock()
_step_counters: dict[str, int] = defaultdict(int)


class StepRecorder:
    """Collects tool calls and LLM usage for the node currently running."""

    def __init__(self, run_id: str = "") -> None:
        self.run_id = run_id
        self.tool_calls: list[dict] = []
        self.usage = Usage()


_current: ContextVar[StepRecorder | None] = ContextVar("rfp_step_recorder", default=None)


def now_iso() -> str:
    """Current UTC time as ISO 8601."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_run_id() -> str:
    """A sortable, unique run id like 20260710T140000Z-1a2b3c."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:6]}"


def trace_path(run_id: str) -> Path:
    """Path of the JSONL trace for a run."""
    return settings.path("logs_dir") / f"trace_{run_id}.jsonl"


def write_step(log: StepLog) -> None:
    """Append one StepLog line to the run's trace file."""
    path = trace_path(log.run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock, open(path, "a", encoding="utf-8") as fh:
        fh.write(log.model_dump_json() + "\n")


def read_trace(run_id: str) -> list[StepLog]:
    """Load all StepLogs of a run (empty if the trace does not exist)."""
    path = trace_path(run_id)
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as fh:
        return [StepLog.model_validate_json(line) for line in fh if line.strip()]


def summarize_run(steps: list[StepLog]) -> RunSummary:
    """Totals over the StepLogs of one run (latency is the sum of step latencies)."""
    calls = [call for step in steps for call in step.tool_calls]
    return RunSummary(
        steps=len(steps),
        latency_ms=sum(s.latency_ms for s in steps),
        tokens_in=sum(s.tokens_in for s in steps),
        tokens_out=sum(s.tokens_out for s in steps),
        cost_usd=round(sum(s.cost_usd for s in steps), 6),
        llm_calls=sum(1 for c in calls if c.get("kind") == "llm"),
        searches=sum(1 for c in calls if c.get("kind") == "search"),
        errors=sum(1 for s in steps if s.error),
    )


def record_tool_call(kind: str, **details: Any) -> None:
    """Record a tool call (e.g. a search) on the running node, if any."""
    recorder = _current.get()
    if recorder is not None:
        recorder.tool_calls.append({"kind": kind, **details})


def record_llm_call(model: str, usage: Usage, latency_ms: int, **details: Any) -> None:
    """Record an LLM call and add its usage to the running node, if any."""
    recorder = _current.get()
    if recorder is None:
        return
    recorder.usage = recorder.usage + usage
    recorder.tool_calls.append(
        {
            "kind": "llm",
            "model": model,
            "tokens_in": usage.tokens_in,
            "tokens_out": usage.tokens_out,
            "cost_usd": round(usage.cost_usd, 6),
            "latency_ms": latency_ms,
            **details,
        }
    )


def summarize(value: Any, limit: int = _SUMMARY_LIMIT) -> str:
    """Short human-readable description of a state dict or value."""
    if isinstance(value, dict):
        text = ", ".join(f"{key}={_short(val)}" for key, val in value.items())
    else:
        text = _short(value, width=limit)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _short(value: Any, width: int = 80) -> str:
    """One-token description of a value: text prefix, size or type."""
    if value is None or isinstance(value, (bool, int, float)):
        return repr(value)
    if isinstance(value, str):
        return repr(value if len(value) <= width else value[: width - 3] + "...")
    if isinstance(value, (list, tuple, set)):
        return f"{type(value).__name__}[{len(value)}]"
    if isinstance(value, dict):
        return f"dict[{len(value)}]"
    if isinstance(value, BaseModel):
        return type(value).__name__
    return type(value).__name__


def _next_step(run_id: str) -> int:
    """Next 1-based step number within a run (thread-safe)."""
    with _lock:
        _step_counters[run_id] += 1
        return _step_counters[run_id]


# ---------------------------------------------------------------- cancellation

class RunCancelled(Exception):
    """Raised inside a graph node when its run was asked to stop."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"run {run_id} was cancelled")


_cancelled: set[str] = set()


def request_cancel(run_id: str) -> None:
    """Ask a run to stop at its next node boundary.

    A run is a thread, so it cannot be killed: `log_step` checks this flag
    before each node and raises `RunCancelled`, which unwinds the graph. Work
    already in flight inside the current node finishes first.
    """
    with _lock:
        _cancelled.add(run_id)


def is_cancel_requested(run_id: str) -> bool:
    """Has this run been asked to stop?"""
    with _lock:
        return run_id in _cancelled


def clear_cancel(run_id: str) -> None:
    """Forget a run's cancel flag once it has stopped."""
    with _lock:
        _cancelled.discard(run_id)


def raise_if_cancelled() -> None:
    """Raise RunCancelled when the node currently running was asked to stop.

    `llm.py` calls this before every provider request, so a stop request takes
    effect within one in-flight call instead of waiting for the whole node.
    Outside a node (CLI helpers, tests without a recorder) it does nothing.
    """
    recorder = _current.get()
    if recorder is not None and recorder.run_id and is_cancel_requested(recorder.run_id):
        raise RunCancelled(recorder.run_id)


def log_step(agent: str, node: str | None = None) -> Callable:
    """Wrap a graph node `(state) -> dict` with tracing and error capture.

    The wrapped node never raises: an exception becomes an entry in the
    returned `errors` list and the StepLog's `error` field. The StepLog is
    always appended to the returned `usage` list.
    """

    def decorator(fn: Callable[..., dict | None]) -> Callable[..., dict]:
        node_name = node or fn.__name__

        @functools.wraps(fn)
        def wrapper(state: dict, *args: Any, **kwargs: Any) -> dict:
            # Checked before the try below on purpose: that block turns every
            # exception into a logged error and lets the graph carry on, which
            # is right for a failing node and wrong for a cancelled run.
            if is_cancel_requested(str(state.get("run_id") or "")):
                raise RunCancelled(str(state.get("run_id")))
            recorder = StepRecorder(str(state.get("run_id") or ""))
            token = _current.set(recorder)
            started_at, t0 = now_iso(), time.perf_counter()
            error: str | None = None
            try:
                result = dict(fn(state, *args, **kwargs) or {})
            except RunCancelled:
                # A stop request must unwind the graph, not become a logged
                # error that the run carries on past.
                _current.reset(token)
                raise
            except Exception as exc:  # noqa: BLE001 - a node must never crash the run
                error = f"{type(exc).__name__}: {exc}"
                result = {"errors": [f"{node_name}: {error}"]}
                _current.reset(token)
            else:
                _current.reset(token)

            run_id = str(state.get("run_id") or "no-run")
            log = StepLog(
                run_id=run_id,
                step=_next_step(run_id),
                agent=agent,
                node=node_name,
                started_at=started_at,
                input_summary=summarize(state),
                tool_calls=recorder.tool_calls,
                output_summary=summarize(result),
                tokens_in=recorder.usage.tokens_in,
                tokens_out=recorder.usage.tokens_out,
                cost_usd=round(recorder.usage.cost_usd, 6),
                latency_ms=int((time.perf_counter() - t0) * 1000),
                error=error,
            )
            write_step(log)
            result["usage"] = list(result.get("usage", [])) + [log]
            return result

        return wrapper

    return decorator
