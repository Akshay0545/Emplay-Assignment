"""Ingestion agent node: parse and incrementally index each bid folder."""

from __future__ import annotations

from rfp_intel.agents.runtime import bid_folder, get_engine
from rfp_intel.logging_utils import RunCancelled, is_cancel_requested, log_step, record_tool_call
from rfp_intel.search.engine import IndexCancelled


@log_step(agent="Ingestion", node="ingest")
def ingest(state: dict) -> dict:
    """Index every bid in `bid_ids`; a missing or failing bid is reported, not fatal."""
    errors: list[str] = []
    run_id = str(state.get("run_id") or "")
    should_stop = (lambda: is_cancel_requested(run_id)) if run_id else None
    for bid_id in state.get("bid_ids", []):
        folder = bid_folder(bid_id)
        if not folder.is_dir():
            errors.append(f"ingest: bid folder not found: {folder}")
            continue
        try:
            stats = get_engine().index_bid(folder, should_stop=should_stop)
        except IndexCancelled:
            raise RunCancelled(run_id)
        except Exception as exc:  # noqa: BLE001 - one bad bid must not stop the run
            errors.append(f"ingest: {bid_id}: {type(exc).__name__}: {exc}")
            continue
        record_tool_call("index_bid", bid_id=bid_id, documents=stats.documents,
                         chunks_added=stats.chunks_added, chunks_total=stats.chunks_total,
                         skipped=stats.skipped, warnings=stats.warnings)
    return {"errors": errors}
