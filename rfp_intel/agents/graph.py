"""LangGraph wiring: build_extraction_graph() and build_qa_graph() (BUILD_PLAN 6.4).

Extraction:
    START -> ingest -> orchestrate -> [Send per field group] field_group -> reconcile
          -> validate --(to_retry and retries left)--> [Send] field_group (widened) -> ...
                      --(else)--> report -> END
    `field_group` runs the sub-graph  retrieve -> extract_<group>  so the Retrieval
    agent always runs before an extractor and the three groups run in parallel.

Q&A:
    START -> orchestrate -> retrieve -> answer -> validate_answer
          --(unsupported, first time)--> answer (same evidence + validator feedback) -> validate_answer
          --(else)--> END
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from rfp_intel.agents.nodes.answer import answer, validate_answer
from rfp_intel.agents.nodes.compare import compare
from rfp_intel.agents.nodes.go_no_go import go_no_go
from rfp_intel.agents.nodes.extract import EXTRACT_GROUPS, EXTRACT_NODES
from rfp_intel.agents.nodes.ingest import ingest
from rfp_intel.agents.nodes.orchestrate import orchestrate
from rfp_intel.agents.nodes.reconcile import reconcile
from rfp_intel.agents.nodes.report import report
from rfp_intel.agents.nodes.retrieve import retrieve
from rfp_intel.agents.nodes.validate import validate
from rfp_intel.agents.state import GraphState
from rfp_intel.logging_utils import new_run_id
from rfp_intel.schemas import Plan, Task

_BRANCH_KEYS = ("run_id", "mode", "bid_ids", "plan", "retries")


class GroupState(GraphState, total=False):
    """State of one field-group branch."""

    task: Task


def build_group_graph():
    """retrieve -> extract_<group> for one task."""
    graph = StateGraph(GroupState)
    graph.add_node("retrieve", retrieve)
    for group in EXTRACT_GROUPS:
        graph.add_node(f"extract_{group}", EXTRACT_NODES[group])
    graph.add_edge(START, "retrieve")
    graph.add_conditional_edges("retrieve", lambda s: f"extract_{s['task'].group}",
                                {f"extract_{g}": f"extract_{g}" for g in EXTRACT_GROUPS})
    for group in EXTRACT_GROUPS:
        graph.add_edge(f"extract_{group}", END)
    return graph.compile()


_group_graph = None


def field_group(payload: dict) -> dict:
    """Run one group branch and return only the reducer-merged keys."""
    global _group_graph
    if _group_graph is None:
        _group_graph = build_group_graph()
    result = _group_graph.invoke(payload)
    return {key: result.get(key, default) for key, default in
            (("evidence", {}), ("drafts", {}), ("usage", []), ("errors", []))}


def _branch_payload(state: dict, task: Task, widened: bool) -> dict:
    """What a group branch needs (never the parent's reducer keys)."""
    payload = {key: state[key] for key in _BRANCH_KEYS if key in state}
    payload.update({"task": task, "widened": widened, "to_retry": list(state.get("to_retry", []))})
    return payload


def fan_out(state: dict) -> list[Send]:
    """One Send per task of the plan."""
    plan: Plan | None = state.get("plan")
    return [Send("field_group", _branch_payload(state, task, widened=False)) for task in (plan.tasks if plan else [])]


def retry_or_report(state: dict) -> list[Send] | str:
    """Retry the groups that own failed fields (widened), or finish."""
    plan: Plan | None = state.get("plan")
    to_retry = set(state.get("to_retry") or [])
    if not to_retry or plan is None:
        return "report"
    sends = [Send("field_group", _branch_payload(state, task, widened=True))
             for task in plan.tasks if to_retry & set(task.fields)]
    return sends or "report"


def build_extraction_graph():
    """The compiled extraction graph."""
    graph = StateGraph(GraphState)
    graph.add_node("ingest", ingest)
    graph.add_node("orchestrate", orchestrate)
    graph.add_node("field_group", field_group)
    graph.add_node("reconcile", reconcile)
    graph.add_node("validate", validate)
    graph.add_node("report", report)
    graph.add_edge(START, "ingest")
    graph.add_edge("ingest", "orchestrate")
    graph.add_conditional_edges("orchestrate", fan_out, ["field_group"])
    graph.add_edge("field_group", "reconcile")
    graph.add_edge("reconcile", "validate")
    graph.add_conditional_edges("validate", retry_or_report, ["field_group", "report"])
    graph.add_edge("report", END)
    return graph.compile()


def build_qa_graph():
    """The compiled question-answering graph."""
    graph = StateGraph(GraphState)
    graph.add_node("orchestrate", orchestrate)
    graph.add_node("retrieve", retrieve)
    graph.add_node("answer", answer)
    graph.add_node("validate_answer", validate_answer)
    graph.add_edge(START, "orchestrate")
    graph.add_edge("orchestrate", "retrieve")
    graph.add_edge("retrieve", "answer")
    graph.add_edge("answer", "validate_answer")
    # The retry redrafts on the evidence already in state: Q&A retrieval is not
    # widened in any useful way, so searching again would only repeat the work.
    graph.add_conditional_edges("validate_answer", lambda s: "answer" if s.get("to_retry") else END,
                                {"answer": "answer", END: END})
    return graph.compile()


def build_compare_graph():
    """START -> compare -> END (bonus: side-by-side report from saved records)."""
    graph = StateGraph(GraphState)
    graph.add_node("compare", compare)
    graph.add_edge(START, "compare")
    graph.add_edge("compare", END)
    return graph.compile()


def build_go_no_go_graph():
    """START -> go_no_go -> END (bonus: recommendation against config/capabilities.yaml)."""
    graph = StateGraph(GraphState)
    graph.add_node("go_no_go", go_no_go)
    graph.add_edge(START, "go_no_go")
    graph.add_edge("go_no_go", END)
    return graph.compile()


def initial_state(mode: str, bid_ids: list[str], question: str | None = None, run_id: str | None = None) -> dict[str, Any]:
    """A fresh GraphState."""
    return {"run_id": run_id or new_run_id(), "mode": mode, "bid_ids": list(bid_ids), "question": question,
            "evidence": {}, "drafts": {}, "addendum_changes": [], "validation": {}, "retries": {},
            "to_retry": [], "widened": False, "answer": None, "answer_feedback": None, "record": None,
            "comparison": None, "go_no_go": None, "errors": [], "usage": []}


def run_extraction(bid_id: str, run_id: str | None = None) -> dict:
    """Run the extraction graph for one bid; returns the final state."""
    return build_extraction_graph().invoke(initial_state("extract", [bid_id], run_id=run_id))


def run_question(question: str, bid_ids: list[str], run_id: str | None = None) -> dict:
    """Run the Q&A graph; returns the final state (answer in state["answer"])."""
    return build_qa_graph().invoke(initial_state("qa", bid_ids, question=question, run_id=run_id))


def run_comparison(bid_ids: list[str], run_id: str | None = None) -> dict:
    """Run the comparison graph; the report is in state["comparison"]."""
    return build_compare_graph().invoke(initial_state("compare", bid_ids, run_id=run_id))


def run_go_no_go(bid_id: str, run_id: str | None = None) -> dict:
    """Run the go/no-go graph for one bid; the report is in state["go_no_go"]."""
    return build_go_no_go_graph().invoke(initial_state("go_no_go", [bid_id], run_id=run_id))
