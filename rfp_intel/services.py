"""Functions shared by the CLI and the API (Phase 7a, BUILD_PLAN 6.5).

index_bid, search, extract, ask, list_bids, get_record, run_status plus the
helpers the API needs (bid detail, chunk lookup, background extraction and
questions, uploads, safe file paths, eval results). Every function returns a Pydantic
model from rfp_intel.schemas or a plain Python value; the search engine is
always the shared one from rfp_intel.agents.runtime.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Literal

from rfp_intel.agents.graph import run_comparison, run_extraction, run_go_no_go, run_question
from rfp_intel.agents.runtime import get_engine
from rfp_intel.config import ROOT, settings
from rfp_intel.ingestion.loader import SUPPORTED_SUFFIXES, load_bid_folder
from rfp_intel.logging_utils import (RunCancelled, clear_cancel, is_cancel_requested, new_run_id, read_trace,
                                     request_cancel, summarize_run)
from rfp_intel.search.engine import IndexCancelled
from rfp_intel.schemas import (
    Answer,
    BidDetail,
    RemoveResult,
    BidRecord,
    BidSummary,
    Chunk,
    ComparisonReport,
    DocType,
    Document,
    DocumentInfo,
    GoNoGoReport,
    IndexStats,
    RunInfo,
    RunStatus,
    SearchFilters,
    SearchHit,
)

logger = logging.getLogger("rfp_intel.runs")

BID_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
EVAL_RESULTS_DIR = ROOT / "eval" / "results"
_TERMINAL_NODES = {"report", "validate_answer", "compare", "go_no_go"}

# Background run registry: run_id -> {"kind", "bid_id", "status", "error", "answer"}.
# Extractions ("extract") and questions ("qa") share it; entries written by
# older code (no "kind") are read as extractions.
_runs: dict[str, dict[str, Any]] = {}
_runs_lock = threading.Lock()
# bid_id -> run_id of an upload still being parsed and embedded (guarded by _runs_lock).
_indexing: dict[str, str] = {}
# Only one extraction at a time (single Qdrant client, CPU-bound embedding).
_extract_lock = threading.Lock()
# Questions run on a small pool; `_ask_pool` is created on first use so tests and
# the CLI never start threads they do not need.
_ask_pool: ThreadPoolExecutor | None = None
_ask_pool_lock = threading.Lock()
# Process-wide counters for GET /metrics.
_cache_hits = 0
_cache_misses = 0


# ---------------------------------------------------------------- paths

def bid_folder(bid_id: str) -> Path:
    """Folder of a bid under data_dir."""
    return settings.path("data_dir") / bid_id


def record_path(bid_id: str) -> Path:
    """Path of the saved deliverable outputs/<bid>.json."""
    return settings.path("outputs_dir") / f"{bid_id}.json"


def safe_file_path(bid_id: str, file_name: str) -> Path:
    """Resolve data_dir/bid_id/file_name; raise ValueError if it escapes the bid folder."""
    if not BID_ID_PATTERN.match(bid_id):
        raise ValueError(f"invalid bid id {bid_id!r}")
    base = bid_folder(bid_id).resolve()
    candidate = (base / file_name).resolve()
    if candidate == base or base not in candidate.parents:
        raise ValueError("file path escapes the bid folder")
    return candidate


# ---------------------------------------------------------------- index / search

def index_bid(folder: Path, force: bool = False,
              should_stop: Callable[[], bool] | None = None) -> IndexStats:
    """Index one bid folder with the shared engine; `should_stop` makes it cancellable."""
    folder = Path(folder)
    if not folder.is_dir():
        raise FileNotFoundError(f"bid folder not found: {folder}")
    return get_engine().index_bid(folder, force=force, should_stop=should_stop)


def search(query: str, bid_id: str | None = None, doc_type: str | None = None,
           addendum: int | None = None, top_k: int | None = None,
           config: str | None = None) -> list[SearchHit]:
    """Hybrid search with optional filters; ValueError for a bad doc_type or config."""
    settings.retrieval_for(config)  # raises ValueError for an unknown named config
    doc_types = [DocType(doc_type)] if doc_type else None  # ValueError for unknown types
    filters = SearchFilters(bid_id=bid_id or None, doc_types=doc_types, addendum_number=addendum)
    return get_engine().search(query, filters=filters, top_k=top_k, config=config)


# ---------------------------------------------------------------- extraction / Q&A

def extract(bid_id: str, run_id: str | None = None) -> BidRecord:
    """Index the bid folder, run the extraction graph and return the BidRecord."""
    folder = bid_folder(bid_id)
    if not folder.is_dir():
        raise FileNotFoundError(f"bid folder not found: {folder}")
    index_bid(folder, should_stop=(lambda: is_cancel_requested(run_id)) if run_id else None)
    state = run_extraction(bid_id, run_id=run_id)
    record = state.get("record")
    if record is None:
        errors = "; ".join(state.get("errors", [])) or "no record produced"
        raise RuntimeError(f"extraction failed: {errors}")
    return record


def ask(question: str, bid_ids: list[str] | None = None, run_id: str | None = None) -> Answer:
    """Answer a free-form question over the given bids (default: every indexed bid).

    With `cache.enabled`, an equivalent question already answered for the same
    bids and the same index state is served from the semantic answer cache.
    """
    global _cache_hits, _cache_misses
    engine = get_engine()
    scope = list(bid_ids or [])
    bids = scope or list(engine.known_bids())
    cache = getattr(engine, "answer_cache", None) if settings.cache.enabled else None
    fingerprint = engine.index_fingerprint(scope) if cache is not None else ""
    if cache is not None:
        hit = _cache_lookup(cache, question, scope, fingerprint)
        if hit is not None:
            _cache_hits += 1
            logger.info("ask cached run_id=%s bids=%s", run_id or "-", ",".join(bids) or "*")
            return hit
        _cache_misses += 1
    state = run_question(question, bids, run_id=run_id)
    answer = state.get("answer")
    if answer is None:
        return Answer(question=question, answer="Not found in documents", citations=[],
                      confidence=0.0, bids=bids)
    if cache is not None:
        try:
            cache.put(question, scope, fingerprint, answer)
        except Exception as exc:  # noqa: BLE001 - a cache failure must not fail the answer
            logger.warning("answer cache write failed: %s: %s", type(exc).__name__, exc)
    return answer


def _cache_lookup(cache: Any, question: str, scope: list[str], fingerprint: str) -> Answer | None:
    """Cache lookup that never raises (a broken cache means a miss)."""
    try:
        return cache.get(question, scope, fingerprint)
    except Exception as exc:  # noqa: BLE001
        logger.warning("answer cache lookup failed: %s: %s", type(exc).__name__, exc)
        return None


def clear_answer_cache() -> int:
    """Delete every cached answer; returns how many there were."""
    return int(get_engine().answer_cache.clear())


def start_extraction(bid_id: str) -> str:
    """Start `extract` in a daemon thread; return its run_id.

    Raises FileNotFoundError for an unknown bid and RuntimeError when another
    extraction is still running.
    """
    if not bid_folder(bid_id).is_dir():
        raise FileNotFoundError(f"bid folder not found: {bid_folder(bid_id)}")
    if not _extract_lock.acquire(blocking=False):
        raise ExtractionBusy(active_run())
    run_id = new_run_id()
    _register_run(run_id, "extract", bid_id)

    def worker() -> None:
        try:
            extract(bid_id, run_id=run_id)
            _set_run(run_id, "done")
        except Exception as exc:  # noqa: BLE001 - a failed run is reported, never raised
            _finish_interrupted(run_id, exc)
        finally:
            clear_cancel(run_id)
            _extract_lock.release()

    threading.Thread(target=worker, name=f"extract-{run_id}", daemon=True).start()
    return run_id


def _finish_interrupted(run_id: str, exc: BaseException) -> None:
    """Mark a run that ended on an exception as cancelled or failed.

    LangGraph can wrap a node's exception, so the cancel flag is the source of
    truth rather than the exception type alone.
    """
    if isinstance(exc, RunCancelled) or is_cancel_requested(run_id):
        _set_run(run_id, "cancelled", "stopped on request")
        logger.info("run %s cancelled on request", run_id)
    else:
        _set_run(run_id, "failed", f"{type(exc).__name__}: {exc}")


def cancel_run(run_id: str) -> RunInfo:
    """Ask a running run to stop at its next node boundary.

    Returns the run's info. KeyError when the run is unknown to this process,
    RuntimeError when it has already finished.
    """
    with _runs_lock:
        entry = _runs.get(run_id)
        if entry is None:
            raise KeyError(run_id)
        status = entry["status"]
    if status != "running":
        raise RuntimeError(f"run {run_id} is already {status}")
    request_cancel(run_id)
    logger.info("cancel requested for run %s", run_id)
    with _runs_lock:
        return _run_info(run_id, _runs[run_id])


def start_indexing(folder: Path) -> str:
    """Index a bid folder in a daemon thread; return its run_id.

    The caller (POST /bids) returns as soon as the files are on disk, so a large
    PDF is parsed and embedded without holding the request open. While it runs
    the bid is listed with `indexing=True` and refuses removal.
    """
    folder = Path(folder)
    bid_id = folder.name
    run_id = new_run_id()
    _register_run(run_id, "index", bid_id)
    with _runs_lock:
        _indexing[bid_id] = run_id

    def worker() -> None:
        stats = None
        try:
            stats = index_bid(folder, should_stop=lambda: is_cancel_requested(run_id))
            _set_run(run_id, "done")
            logger.info("indexed %s: %d documents, %d chunks in %.1fs",
                        bid_id, stats.documents, stats.chunks_total, stats.seconds)
        except IndexCancelled:
            _set_run(run_id, "cancelled", "stopped on request")
            logger.info("indexing of %s stopped on request (run %s)", bid_id, run_id)
        except Exception as exc:  # noqa: BLE001 - a failed index is reported, never raised
            _set_run(run_id, "failed", f"{type(exc).__name__}: {exc}")
        finally:
            with _runs_lock:
                _indexing.pop(bid_id, None)
        # Only after the index run is marked done, so the UI sees indexing
        # finish before the extraction appears. A cancel that arrived too late
        # to interrupt the index (e.g. from remove_bid) still stops here.
        if stats is not None and not is_cancel_requested(run_id):
            _auto_extract(bid_id, stats)
        clear_cancel(run_id)

    threading.Thread(target=worker, name=f"index-{bid_id}", daemon=True).start()
    return run_id


def _auto_extract(bid_id: str, stats: IndexStats) -> None:
    """Start the extraction for a freshly uploaded bid (api.auto_extract_after_upload).

    Every failure here only skips the auto-start: the upload itself succeeded,
    and the user can still press Run extraction.
    """
    if not settings.api.auto_extract_after_upload:
        return
    if stats.skipped:
        logger.info("auto-extract skipped for %s: the folder was unchanged", bid_id)
        return
    try:
        run_id = start_extraction(bid_id)
        logger.info("auto-extract started for %s as run %s", bid_id, run_id)
    except ExtractionBusy as busy:
        logger.info("auto-extract skipped for %s: %s", bid_id, busy)
    except Exception as exc:  # noqa: BLE001 - never turn this into an upload failure
        logger.warning("auto-extract could not start for %s: %s: %s", bid_id, type(exc).__name__, exc)


def indexing_bids() -> set[str]:
    """Bid ids whose upload is still being parsed and embedded."""
    with _runs_lock:
        return set(_indexing)


def start_question(question: str, bid_ids: list[str] | None = None) -> str:
    """Answer a question in the background; return its run_id (poll with `run_status`).

    Questions share a pool of `api.ask_workers` threads. When `api.ask_queue_max`
    questions are already waiting for a thread, `AskQueueFull` is raised (the API
    turns it into 429). A question still running after `agents.qa_timeout_s` is
    reported as failed; its thread finishes in the background and its late
    result is discarded, because a Python thread cannot be interrupted.
    """
    cfg = settings.api
    with _runs_lock:
        waiting = sum(1 for e in _runs.values() if e.get("kind") == "qa" and e["status"] == "running")
        if waiting >= cfg.ask_workers + cfg.ask_queue_max:
            raise AskQueueFull(waiting)
        run_id = new_run_id()
        _runs[run_id] = _new_entry("qa", ",".join(bid_ids or []))

    def worker() -> None:
        try:
            answer = ask(question, bid_ids, run_id=run_id)
        except Exception as exc:  # noqa: BLE001 - a failed run is reported, never raised
            if isinstance(exc, RunCancelled) or is_cancel_requested(run_id):
                _set_run(run_id, "cancelled", "stopped on request", only_if_running=True)
            else:
                _set_run(run_id, "failed", f"{type(exc).__name__}: {exc}", only_if_running=True)
        else:
            _set_run(run_id, "done", answer=answer, only_if_running=True)
        finally:
            clear_cancel(run_id)

    def watchdog() -> None:
        _set_run(run_id, "failed", f"timed out after {settings.agents.qa_timeout_s:g} s", only_if_running=True)

    timer = threading.Timer(settings.agents.qa_timeout_s, watchdog)
    timer.daemon = True
    timer.start()
    _pool().submit(worker).add_done_callback(lambda _f: timer.cancel())
    return run_id


def metrics() -> dict[str, Any]:
    """Counts and latency percentiles of the runs this process has finished, plus cache and queue."""
    with _runs_lock:
        entries = [dict(e) for e in _runs.values()]
    out: dict[str, Any] = {"runs": {}, "ask_queue": ask_queue_stats(),
                           "cache": {"enabled": settings.cache.enabled, "hits": _cache_hits, "misses": _cache_misses}}
    for kind in ("extract", "qa", "compare", "go_no_go"):
        mine = [e for e in entries if (e.get("kind") or "extract") == kind]
        durations = sorted(int(e["duration_ms"]) for e in mine if e.get("duration_ms") is not None)
        out["runs"][kind] = {
            "running": sum(1 for e in mine if e["status"] == "running"),
            "done": sum(1 for e in mine if e["status"] == "done"),
            "failed": sum(1 for e in mine if e["status"] == "failed"),
            "p50_ms": _percentile(durations, 0.5),
            "p95_ms": _percentile(durations, 0.95),
        }
    return out


def _percentile(sorted_values: list[int], q: float) -> int | None:
    """Nearest-rank percentile of an ascending list (None when empty)."""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    return int(statistics.quantiles(sorted_values, n=100, method="inclusive")[max(0, min(98, round(q * 100) - 1))])


def ask_queue_stats() -> dict[str, int]:
    """Background question load: running (<= workers), queued (waiting for a thread), workers."""
    with _runs_lock:
        active = sum(1 for e in _runs.values() if e.get("kind") == "qa" and e["status"] == "running")
    workers = settings.api.ask_workers
    return {"running": min(active, workers), "queued": max(0, active - workers), "workers": workers}


def shutdown_background() -> None:
    """Stop accepting background questions and mark the running ones failed (API shutdown)."""
    global _ask_pool
    with _ask_pool_lock:
        pool, _ask_pool = _ask_pool, None
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=True)
    with _runs_lock:
        for entry in _runs.values():
            if entry.get("kind") == "qa" and entry["status"] == "running":
                entry["status"], entry["error"] = "failed", "server shutting down"


def _pool() -> ThreadPoolExecutor:
    """The shared question pool (created on first use)."""
    global _ask_pool
    with _ask_pool_lock:
        if _ask_pool is None:
            _ask_pool = ThreadPoolExecutor(max_workers=settings.api.ask_workers, thread_name_prefix="ask")
        return _ask_pool


class ExtractionBusy(RuntimeError):
    """Raised by start_extraction while another run holds the extraction lock."""

    def __init__(self, active: RunInfo | None) -> None:
        self.active = active
        detail = f" for bid {active.bid_id!r} (run {active.run_id})" if active else ""
        super().__init__(f"an extraction is already running{detail}")


class AskQueueFull(RuntimeError):
    """Raised by start_question when too many questions are already waiting."""

    def __init__(self, waiting: int) -> None:
        self.waiting = waiting
        super().__init__(f"the server is busy answering {waiting} questions; try again in a moment")


def _new_entry(kind: str, bid_id: str) -> dict[str, Any]:
    """A fresh registry entry."""
    return {"kind": kind, "bid_id": bid_id, "status": "running", "error": None, "answer": None,
            "started": time.perf_counter(), "duration_ms": None}


def _register_run(run_id: str, kind: str, bid_id: str) -> None:
    """Add a running entry to the registry."""
    with _runs_lock:
        _runs[run_id] = _new_entry(kind, bid_id)


def _run_info(run_id: str, entry: dict[str, Any]) -> RunInfo:
    """RunInfo for a registry entry (entries without "kind" are extractions)."""
    return RunInfo(run_id=run_id, bid_id=str(entry.get("bid_id") or ""), status=entry["status"],
                   kind=entry.get("kind") or "extract")


def list_runs() -> list[RunInfo]:
    """Runs started by this API process (extractions and questions), newest first."""
    with _runs_lock:
        items = [_run_info(rid, e) for rid, e in _runs.items()]
    return list(reversed(items))


def active_run() -> RunInfo | None:
    """The extraction currently holding the extraction lock, if this process knows it."""
    return next((r for r in list_runs() if r.status == "running" and r.kind == "extract"), None)


def _set_run(run_id: str, status: str, error: str | None = None, answer: Answer | None = None,
             only_if_running: bool = False) -> None:
    """Update the registry entry of a background run (optionally only while it is still running)."""
    with _runs_lock:
        entry = _runs.setdefault(run_id, _new_entry("extract", ""))
        if only_if_running and entry["status"] != "running":
            return
        entry["status"], entry["error"] = status, error
        if answer is not None:
            entry["answer"] = answer
        if status != "running" and entry.get("started") is not None and entry.get("duration_ms") is None:
            entry["duration_ms"] = int((time.perf_counter() - entry["started"]) * 1000)
        kind, bid_id, duration = entry.get("kind") or "extract", entry.get("bid_id"), entry.get("duration_ms")
    if status != "running":
        logger.info("run %s kind=%s bid=%s run_id=%s duration_ms=%s%s", status, kind, bid_id or "*", run_id,
                    duration, f" error={error!r}" if error else "")


def run_status(run_id: str) -> RunStatus:
    """Progress of a run: registry status when known, else inferred from its trace."""
    steps = read_trace(run_id)
    with _runs_lock:
        entry = dict(_runs.get(run_id, {}))
    if entry:
        info = _run_info(run_id, entry)
        return RunStatus(run_id=run_id, bid_id=info.bid_id, status=info.status, steps=steps, kind=info.kind,
                         answer=entry.get("answer"), error=entry.get("error"), summary=summarize_run(steps))
    if not steps:
        raise KeyError(run_id)
    status = "done" if steps[-1].node in _TERMINAL_NODES and steps[-1].error is None else "failed"
    kind = _kind_of_steps(steps)
    return RunStatus(run_id=run_id, bid_id=_bid_of_run(run_id, kind, steps), status=status, steps=steps, kind=kind,
                     summary=summarize_run(steps))


def _kind_of_steps(steps: list) -> str:
    """Run kind inferred from the nodes of a trace."""
    nodes = {s.node for s in steps}
    if "compare" in nodes:
        return "compare"
    if "go_no_go" in nodes:
        return "go_no_go"
    return "qa" if nodes & {"answer", "validate_answer"} else "extract"


def _bid_of_run(run_id: str, kind: str = "extract", steps: list | None = None) -> str:
    """Bid(s) whose saved output came from this run (empty when unknown).

    Comparison and go/no-go runs are resolved from the ids their node recorded
    in the trace (`outputs/comparison.json` only keeps the latest comparison).
    """
    if kind in ("compare", "go_no_go"):
        for step in steps or []:
            for call in step.tool_calls:
                if kind == "compare" and call.get("bid_ids"):
                    return ",".join(str(b) for b in call["bid_ids"])
                if kind == "go_no_go" and call.get("bid_id"):
                    return str(call["bid_id"])
        return ""
    out_dir = settings.path("outputs_dir")
    if not out_dir.is_dir():
        return ""
    for path in sorted(out_dir.glob("*.json")):
        record = _load_record(path)
        if record is not None and record.run_id == run_id:
            return record.bid_id
    return ""


# ---------------------------------------------------------------- bids and records

def _load_record(path: Path) -> BidRecord | None:
    """Parse a saved BidRecord; None when missing or malformed."""
    if not path.is_file():
        return None
    try:
        return BidRecord.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def get_record(bid_id: str) -> BidRecord | None:
    """The latest saved BidRecord of a bid (outputs/<bid>.json), or None."""
    return _load_record(record_path(bid_id))


def _value_text(record: BidRecord, field: str) -> str | None:
    """A field's value as text (lists joined with '; ')."""
    item = record.fields.get(field)
    if item is None or item.value is None:
        return None
    return "; ".join(item.value) if isinstance(item.value, list) else str(item.value)


def _due_date_changed_by(record: BidRecord) -> int | None:
    """Addendum number of the latest override of "Due Date", if any."""
    overrides = [c for c in record.addendum_changes if c.field == "Due Date" and c.kind == "override"]
    return overrides[-1].addendum_number if overrides else None


def _input_files(folder: Path) -> list[Path]:
    """Supported input files of a bid folder."""
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES)


def list_bids() -> list[BidSummary]:
    """One summary per folder under data_dir, enriched from the saved record."""
    data_dir = settings.path("data_dir")
    if not data_dir.is_dir():
        return []
    indexed = set(get_engine().known_bids())
    busy = indexing_bids()
    summaries: list[BidSummary] = []
    for folder in sorted(p for p in data_dir.iterdir() if p.is_dir()):
        record = get_record(folder.name)
        summaries.append(BidSummary(
            bid_id=folder.name,
            title=_value_text(record, "Title") if record else None,
            agency=_value_text(record, "company_name") if record else None,
            due_date=_value_text(record, "Due Date") if record else None,
            due_date_changed_by=_due_date_changed_by(record) if record else None,
            doc_count=len(_input_files(folder)),
            indexed=folder.name in indexed,
            indexing=folder.name in busy,
            last_run=record.generated_at if record else None,
            validation=dict(record.validation) if record else None,
        ))
    return summaries


def _parsed_documents(bid_id: str) -> list[Document] | None:
    """Documents from outputs/parsed/<bid>/*.json when every file parses, else None."""
    parsed_dir = settings.path("outputs_dir") / "parsed" / bid_id
    paths = sorted(parsed_dir.glob("*.json")) if parsed_dir.is_dir() else []
    if not paths:
        return None
    try:
        return [Document.model_validate_json(p.read_text(encoding="utf-8")) for p in paths]
    except (ValueError, OSError):
        return None


def _bid_chunks(bid_id: str) -> list[Chunk]:
    """Indexed chunks of a bid from the engine's BM25 corpus (empty when not indexed)."""
    store = getattr(get_engine(), "bm25", None)
    if store is None:
        return []
    chunks, _tokens = store._load(bid_id)  # noqa: SLF001 - corpus access by id
    return list(chunks)


def get_bid_detail(bid_id: str) -> BidDetail:
    """Documents of a bid (type, pages, tables, warnings) plus index state."""
    folder = bid_folder(bid_id)
    if not folder.is_dir():
        raise FileNotFoundError(f"bid folder not found: {folder}")
    documents = _parsed_documents(bid_id) or load_bid_folder(folder)
    infos = [DocumentInfo(
        file_name=doc.file_name, doc_type=doc.doc_type, addendum_number=doc.addendum_number,
        pages=len(doc.pages), tables=sum(len(page.tables) for page in doc.pages),
        warnings=list(doc.warnings),
    ) for doc in documents]
    indexed = bid_id in set(get_engine().known_bids())
    return BidDetail(bid_id=bid_id, documents=infos, indexed=indexed,
                     chunks=len(_bid_chunks(bid_id)) if indexed else 0,
                     indexing=bid_id in indexing_bids())


def get_chunk(chunk_id: str) -> Chunk | None:
    """Look a chunk up by id across every indexed bid."""
    for bid_id in get_engine().known_bids():
        for chunk in _bid_chunks(bid_id):
            if chunk.chunk_id == chunk_id:
                return chunk
    return None


# ---------------------------------------------------------------- bonus: comparison and go / no-go

def compare_bids(bid_ids: list[str], run_id: str | None = None) -> ComparisonReport:
    """Side-by-side report of two or more bids from their saved records.

    ValueError for fewer than two distinct bids, FileNotFoundError when a bid
    has no record yet, RuntimeError when the node produced no report.
    """
    ids = list(dict.fromkeys(b.strip() for b in bid_ids if b and b.strip()))
    if len(ids) < 2:
        raise ValueError("compare needs at least two different bid ids")
    for bid_id in ids:
        if get_record(bid_id) is None:
            raise FileNotFoundError(f"no extraction record for {bid_id!r}; run the extraction first")
    run_id = run_id or new_run_id()
    _register_run(run_id, "compare", ",".join(ids))
    try:
        state = run_comparison(ids, run_id=run_id)
    except Exception as exc:  # noqa: BLE001 - mark the run, then re-raise
        _set_run(run_id, "failed", f"{type(exc).__name__}: {exc}")
        raise
    report = state.get("comparison")
    if report is None:
        error = "comparison failed: " + ("; ".join(state.get("errors", [])) or "no report produced")
        _set_run(run_id, "failed", error)
        raise RuntimeError(error)
    _set_run(run_id, "done")
    return report


def latest_comparison() -> ComparisonReport | None:
    """The last saved outputs/comparison.json, or None."""
    path = settings.path("outputs_dir") / "comparison.json"
    if not path.is_file():
        return None
    try:
        return ComparisonReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def assess_go_no_go(bid_id: str, run_id: str | None = None) -> GoNoGoReport:
    """Go / no-go recommendation for a bid against config/capabilities.yaml.

    FileNotFoundError when the bid has no record yet, RuntimeError when the
    node produced no report.
    """
    if get_record(bid_id) is None:
        raise FileNotFoundError(f"no extraction record for {bid_id!r}; run the extraction first")
    run_id = run_id or new_run_id()
    _register_run(run_id, "go_no_go", bid_id)
    try:
        state = run_go_no_go(bid_id, run_id=run_id)
    except Exception as exc:  # noqa: BLE001 - mark the run, then re-raise
        _set_run(run_id, "failed", f"{type(exc).__name__}: {exc}")
        raise
    report = state.get("go_no_go")
    if report is None:
        error = "go/no-go failed: " + ("; ".join(state.get("errors", [])) or "no report produced")
        _set_run(run_id, "failed", error)
        raise RuntimeError(error)
    _set_run(run_id, "done")
    return report


def get_go_no_go(bid_id: str) -> GoNoGoReport | None:
    """The saved outputs/<bid>_go_no_go.json, or None."""
    if not BID_ID_PATTERN.match(bid_id or ""):
        return None
    path = settings.path("outputs_dir") / f"{bid_id}_go_no_go.json"
    if not path.is_file():
        return None
    try:
        return GoNoGoReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


# ---------------------------------------------------------------- eval and uploads

def eval_results(kind: Literal["retrieval", "extraction"]) -> dict | None:
    """Latest eval/results/<kind>.json, or None when it has not been written."""
    if kind not in ("retrieval", "extraction"):
        raise ValueError(f"unknown eval kind {kind!r}")
    path = EVAL_RESULTS_DIR / f"{kind}.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def remove_bid(bid_id: str) -> RemoveResult:
    """Delete a bid everywhere: index, saved outputs (record, summaries, parsed files) and its data folder.

    A running extraction or upload-indexing of this bid is cancelled first and
    given `_STOP_WAIT_SECONDS` to unwind. ValueError for a bad id,
    FileNotFoundError when the bid is neither on disk nor indexed, RuntimeError
    while a run is still stopping, while the index is busy, or while Windows
    keeps a file locked after retries - all cases where a retry makes sense.
    """
    if not BID_ID_PATTERN.match(bid_id or ""):
        raise ValueError("bid_id must match ^[A-Za-z0-9_-]{1,64}$")
    _stop_runs_for(bid_id)
    folder = bid_folder(bid_id)
    indexed = get_engine().remove_bid(bid_id)
    if not folder.is_dir() and not indexed and not record_path(bid_id).exists():
        raise FileNotFoundError(f"unknown bid: {bid_id}")
    removed_files = _remove_outputs(bid_id)
    if folder.is_dir():
        _delete_retrying(lambda: shutil.rmtree(folder), folder)
    logger.info("removed bid %s (indexed=%s, outputs=%d, folder=%s)", bid_id, indexed, removed_files, folder)
    return RemoveResult(bid_id=bid_id, indexed=indexed, outputs_removed=removed_files, folder_removed=True)


# How long remove_bid waits for a cancelled run to unwind. Cancellation is
# cooperative (checked before every LLM request, between retrieval fields and
# between embedding batches), so a run normally stops within a few seconds;
# only a provider request already in flight can take longer.
_STOP_WAIT_SECONDS = 15.0
_STOP_POLL_INTERVAL = 0.25


def _stop_runs_for(bid_id: str) -> None:
    """Cancel every run of a bid (extraction, upload-indexing) and wait until none is active.

    Re-checks in a loop so a follow-up run (an auto-extract that started just
    as the indexing it waited on finished) is cancelled too. RuntimeError when
    something is still stopping after `_STOP_WAIT_SECONDS`; the cancel flags
    stay set, so a retry a moment later usually finds the runs already stopped.
    """
    deadline = time.monotonic() + _STOP_WAIT_SECONDS
    while True:
        busy: list[str] = []
        running = active_run()
        if running is not None and running.bid_id == bid_id:
            request_cancel(running.run_id)
            busy.append(running.run_id)
        with _runs_lock:
            indexing_run = _indexing.get(bid_id)
        if indexing_run is not None:
            request_cancel(indexing_run)
            busy.append(indexing_run)
        if not busy:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"{bid_id} is being stopped (run {busy[0]} is finishing its current step); "
                "try again in a moment")
        time.sleep(_STOP_POLL_INTERVAL)


# On Windows a freshly written file can be held open briefly by a virus
# scanner, the search indexer or a PDF viewer; deleting it then raises
# PermissionError (WinError 32). Such locks usually clear within seconds.
_LOCK_RETRY_SECONDS = 6.0
_LOCK_RETRY_INTERVAL = 0.5


def _delete_retrying(delete: Callable[[], None], target: Path) -> None:
    """Run a no-arg filesystem delete, retrying while the OS reports a file in use.

    If the lock outlasts the retries, raise RuntimeError with a message naming
    the path, so the API answers 409 ("try again") instead of a bare 500.
    """
    deadline = time.monotonic() + _LOCK_RETRY_SECONDS
    while True:
        try:
            delete()
            return
        except PermissionError as exc:
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"could not delete {target}: a file is in use by another program "
                    f"(a PDF viewer or a scan in progress); close it and try again"
                ) from exc
            time.sleep(_LOCK_RETRY_INTERVAL)


def _remove_outputs(bid_id: str) -> int:
    """Delete outputs/<bid>*.json|md and outputs/parsed/<bid>/; returns the file count."""
    out_dir = settings.path("outputs_dir")
    count = 0
    for path in list(out_dir.glob(f"{bid_id}.json")) + list(out_dir.glob(f"{bid_id}_*.json")) \
            + list(out_dir.glob(f"{bid_id}_*.md")):
        _delete_retrying(path.unlink, path)
        count += 1
    parsed = out_dir / "parsed" / bid_id
    if parsed.is_dir():
        count += sum(1 for p in parsed.iterdir() if p.is_file())
        _delete_retrying(lambda: shutil.rmtree(parsed), parsed)
    return count


def save_upload(bid_id: str, files: list[tuple[str, bytes]]) -> Path:
    """Write uploaded (name, bytes) pairs into data_dir/bid_id; ValueError on bad input."""
    if not BID_ID_PATTERN.match(bid_id or ""):
        raise ValueError("bid_id must match ^[A-Za-z0-9_-]{1,64}$")
    if not files:
        raise ValueError("no files uploaded")
    for name, _content in files:
        if not name or "/" in name or "\\" in name or name in (".", "..") or "\x00" in name:
            raise ValueError(f"invalid file name {name!r}")
        if Path(name).suffix.lower() not in SUPPORTED_SUFFIXES:
            raise ValueError(f"unsupported file type {name!r}: only .pdf/.html/.htm are accepted")
    folder = bid_folder(bid_id)
    folder.mkdir(parents=True, exist_ok=True)
    for name, content in files:
        (folder / name).write_bytes(content)
    return folder
