"""Extraction agent nodes, one per field group.

Each node makes one LLM call (EXTRACT_MODEL) for its fields with the numbered
evidence the Retrieval agent collected, and maps evidence ids to citations.
The shared logic lives in agents/baseline.py (Phase 5) so the baseline and
the graph extract fields the same way.
"""

from __future__ import annotations

from typing import Callable

from rfp_intel.agents.baseline import extract_group
from rfp_intel.config import load_fields, settings
from rfp_intel.logging_utils import log_step
from rfp_intel.schemas import EvidenceBundle, SearchHit, Task

EXTRACT_GROUPS = ("dates_logistics", "commercial_legal", "product_specs")


def fields_to_extract(task: Task, state: dict) -> list[str]:
    """All task fields, or only the retried ones during a widened pass."""
    if state.get("widened") and state.get("to_retry"):
        wanted = set(state["to_retry"])
        return [f for f in task.fields if f in wanted]
    return list(task.fields)


def run_extract(state: dict, group: str) -> dict:
    """Extract the group's fields from the evidence in the state."""
    task: Task | None = state.get("task")
    if task is None or task.group != group:
        return {}
    names = fields_to_extract(task, state)
    evidence: dict[str, EvidenceBundle] = state.get("evidence", {})
    hits: dict[str, list[SearchHit]] = {name: evidence[name].hits for name in names if name in evidence}
    specs = [spec for spec in load_fields() if spec.name in names]
    bid_id = task.filters.bid_id or (state.get("bid_ids") or [""])[0]
    values = extract_group(bid_id, group, specs, hits, settings.env.extract_model)
    return {"drafts": values}


def make_extract_node(group: str) -> Callable[[dict], dict]:
    """A traced node `extract_<group>`."""

    @log_step(agent="Extraction", node=f"extract_{group}")
    def node(state: dict) -> dict:
        return run_extract(state, group)

    node.__name__ = f"extract_{group}"
    return node


EXTRACT_NODES = {group: make_extract_node(group) for group in EXTRACT_GROUPS}
