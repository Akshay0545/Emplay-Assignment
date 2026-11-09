"""Orchestrator node: build the Plan for an extraction run (no LLM) or a question (one LLM call)."""

from __future__ import annotations

from rfp_intel import llm
from rfp_intel.agents.runtime import load_prompt
from rfp_intel.config import load_fields, settings
from rfp_intel.logging_utils import RunCancelled, log_step
from rfp_intel.schemas import Plan, QAPlan, SearchFilters, Task

EXTRACT_GROUPS = ("dates_logistics", "commercial_legal", "product_specs")


def extraction_plan(bid_id: str) -> Plan:
    """One Task per field group from config/fields.yaml (the "report" group is not extracted)."""
    tasks = [
        Task(group=group, fields=[f.name for f in load_fields() if f.group == group],
             filters=SearchFilters(bid_id=bid_id))
        for group in EXTRACT_GROUPS
    ]
    return Plan(mode="extract", question_type="extract", tasks=[t for t in tasks if t.fields])


def question_plan(question: str, bid_ids: list[str]) -> Plan:
    """Classify a question with the reasoning model and turn it into one qa Task."""
    prompt = load_prompt("orchestrator").format(question=question, bid_ids=", ".join(bid_ids))
    qa_plan, _ = llm.structured(prompt, QAPlan, settings.env.reason_model)
    bids = [b for b in qa_plan.bids if b in bid_ids] or bid_ids
    filters = SearchFilters(bid_ids=bids, doc_types=qa_plan.doc_types or None)
    queries = [q for q in qa_plan.queries if q.strip()] or [question]
    task = Task(group="qa", queries=queries, filters=filters)
    return Plan(mode="qa", question_type=qa_plan.question_type, tasks=[task])


@log_step(agent="Orchestrator", node="orchestrate")
def orchestrate(state: dict) -> dict:
    """Extraction: a fixed 3-task plan. Q&A: an LLM-classified plan (falls back to a single query)."""
    bid_ids = list(state.get("bid_ids", []))
    if state.get("mode") == "qa":
        question = state.get("question") or ""
        try:
            plan = question_plan(question, bid_ids)
        except RunCancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - planning failure must not stop the answer
            plan = Plan(mode="qa", question_type="single", tasks=[
                Task(group="qa", queries=[question], filters=SearchFilters(bid_ids=bid_ids or None))])
            return {"plan": plan, "errors": [f"orchestrate: fallback plan: {type(exc).__name__}: {exc}"]}
        return {"plan": plan}
    return {"plan": extraction_plan(bid_ids[0]) if bid_ids else Plan(mode="extract", tasks=[])}
