"""Retrieval agent node: the ONLY caller of the search engine.

Extraction: for every field of the task, an EvidenceRequest built from the
field description and search hints (plus the field's doc_types and optional
per-field `top_k`), searched with the bid filter. A second, addendum-only request per field feeds the
Addendum Reconciler. A widened retry uses more query variants, a larger
top_k and no doc-type filter.
Q&A: the plan's queries, searched per bid in scope.

Evidence keys: "<field>" for extraction, "<field>::addendum" for
reconciliation, "qa::<bid>" for questions. An empty result is a bundle with
no hits, never an error.
"""

from __future__ import annotations

import re

from rfp_intel.agents.runtime import get_engine
from rfp_intel.config import FieldSpec, load_fields, settings
from rfp_intel.logging_utils import RunCancelled, is_cancel_requested, log_step, record_tool_call
from rfp_intel.schemas import (
    BidRecord,
    DocType,
    EvidenceBundle,
    EvidenceRequest,
    SearchFilters,
    SearchHit,
    Task,
)
from rfp_intel.search.bm25_store import tokenize
from rfp_intel.search.query import expand_query

ADDENDUM_SUFFIX = "::addendum"
QA_PREFIX = "qa::"
ADDENDUM_TOP_K = 4
MAX_CHANGE_QUERIES = 3          # changes (2 queries each) a change question may add
MAX_QA_EVIDENCE = 20
_ADDENDUM_WORD = re.compile(r"\baddend(?:um|a)\b|\bchange[sd]?\b|\brevis", re.IGNORECASE)
MAX_QUERIES = 6                 # queries per field; one request = one fused search + one re-rank
MAX_PRIMARY_HINTS = 4           # hints joined to the field name in the first query
HINTS_PER_QUERY = 3             # later hints form short queries of this many hints
# Words that say nothing about the topic of a change question.
_STOP_TOKENS = frozenset("""a an and are as at be been by did do does for from has have how in is it its of on or
    that the this to was were what when where which who will with after before all any final original current
    latest new old change changes changed revised revision revisions update updated addendum addenda addendums
    bid bids rfp proposal proposals solicitation document documents date dates time times type types number
    numbers no info information name names detail details required requirement requirements""".split())
MAX_WIDENED_QUERIES = 8
MAX_ADDENDUM_QUERIES = 1
MAX_HINT_WORDS = 12


def evidence_key(field: str, addendum: bool = False) -> str:
    """Evidence dict key for a field (or its addendum evidence)."""
    return f"{field}{ADDENDUM_SUFFIX}" if addendum else field


def field_queries(spec: FieldSpec, widened: bool = False) -> list[str]:
    """Search queries for a field: name + description, then its search hints.

    Normal: the field name with the first hints, a short description query,
    then the remaining hints in groups of HINTS_PER_QUERY (a long joined hint
    list dilutes BM25, so a passage matching one hint would never surface).
    Widened: those plus every hint as its own query, then synonym expansions,
    up to MAX_WIDENED_QUERIES.
    """
    hints = list(spec.search_hints)
    primary = f"{spec.name} {' '.join(hints[:MAX_PRIMARY_HINTS])}".strip()
    description = " ".join(spec.description.split()[:MAX_HINT_WORDS])
    groups = [" ".join(hints[i:i + HINTS_PER_QUERY])
              for i in range(MAX_PRIMARY_HINTS, len(hints), HINTS_PER_QUERY)]
    queries = [primary, description, *groups]
    if widened:
        # the field's own queries and hints first, synonym variants only after them
        queries += hints
        queries += [v for q in queries for v in expand_query(q)]
    limit = MAX_WIDENED_QUERIES if widened else MAX_QUERIES
    return _dedupe(q for q in queries if q.strip())[:limit]


def field_top_k(spec: FieldSpec, widened: bool = False) -> int:
    """Evidence passages for a field: its own `top_k` if set, else the agents setting.

    A widened retry never returns fewer passages than the normal pass.
    """
    cfg = settings.agents
    base = spec.top_k or cfg.evidence_top_k
    return max(base, cfg.widened_top_k) if widened else base


def build_requests(task: Task, widened: bool, only_fields: list[str] | None) -> list[EvidenceRequest]:
    """EvidenceRequests for a task: Q&A queries, or one per field (plus an addendum one)."""
    cfg = settings.agents
    if task.group == "qa":
        top_k = cfg.widened_top_k if widened else cfg.evidence_top_k
        requests = []
        for bid in _bids(task.filters):
            filters = task.filters.model_copy(update={"bid_id": bid, "bid_ids": None})
            extra = change_log_queries(bid, task.queries)
            queries = list(task.queries) + extra
            # keep room for both sides of every change (merged hits are capped at top_k)
            keep = min(MAX_QA_EVIDENCE, top_k + 2 * len(extra))
            requests.append(EvidenceRequest(field_or_question=f"{QA_PREFIX}{bid}", queries=queries,
                                            filters=filters, top_k=keep))
        return requests
    specs = {f.name: f for f in load_fields()}
    requests: list[EvidenceRequest] = []
    for name in task.fields:
        if only_fields is not None and name not in only_fields:
            continue
        spec = specs.get(name)
        if spec is None:
            continue
        doc_types = None if widened else spec.doc_types
        filters = task.filters.model_copy(update={"doc_types": doc_types})
        requests.append(EvidenceRequest(field_or_question=name, queries=field_queries(spec, widened),
                                        filters=filters, top_k=field_top_k(spec, widened)))
        addendum_filters = task.filters.model_copy(update={"doc_types": [DocType.ADDENDUM]})
        requests.append(EvidenceRequest(field_or_question=evidence_key(name, addendum=True),
                                        queries=field_queries(spec)[:MAX_ADDENDUM_QUERIES], filters=addendum_filters,
                                        top_k=ADDENDUM_TOP_K))
    return requests


def change_log_queries(bid_id: str, queries: list[str]) -> list[str]:
    """Extra queries for change questions, derived from the bid's saved change log.

    When a question mentions an addendum, each recorded change *about the
    question's topic* contributes a query for its old value (finds the original
    passage) and its new value (finds the addendum passage), so both sides of
    the change are retrievable. A change is on topic when its field name shares
    a content word with the queries; unrelated changes are skipped so a
    question about one field does not fan out into a search per changed field.
    """
    if not any(_ADDENDUM_WORD.search(q) for q in queries):
        return []
    path = settings.path("outputs_dir") / f"{bid_id}.json"
    if not path.exists():
        return []
    try:
        record = BidRecord.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError:
        return []
    wanted = _content_tokens(" ".join(queries))
    relevant = [c for c in record.addendum_changes if _content_tokens(c.field) & wanted]
    record_tool_call("change_log", bid_id=bid_id, changes=len(record.addendum_changes),
                     relevant=[c.field for c in relevant[:MAX_CHANGE_QUERIES]])
    extra: list[str] = []
    for change in relevant[:MAX_CHANGE_QUERIES]:
        if change.old_value:
            extra.append(f"{change.field} {change.old_value[:80]}")
        extra.append(f"addendum {change.addendum_number} {change.field} {change.new_value[:80]}")
    return _dedupe(extra)


def _content_tokens(text: str) -> set[str]:
    """Lower-case word tokens of `text` without stop-words and generic bid vocabulary."""
    return {t for t in tokenize(text) if t not in _STOP_TOKENS and len(t) > 1}


def run_request(request: EvidenceRequest) -> EvidenceBundle:
    """Search all queries of a request in one fused, re-ranked pass (best first, no duplicates)."""
    engine = get_engine()
    expanded = [variant for query in request.queries for variant in expand_query(query)]
    try:
        hits = engine.search_many(request.queries, request.filters, top_k=request.top_k, config="hybrid_rerank")
    except Exception as exc:  # noqa: BLE001 - a failing search yields no hits, not a crash
        record_tool_call("search", query=request.queries[0] if request.queries else "", queries=list(request.queries),
                         error=f"{type(exc).__name__}: {exc}")
        hits = []
    else:
        record_tool_call("search", query=request.queries[0] if request.queries else "", queries=list(request.queries),
                         filters=request.filters.model_dump(exclude_none=True), top_k=request.top_k, hits=len(hits))
    return EvidenceBundle(request=request, hits=list(hits[:request.top_k]), expanded_queries=_dedupe(expanded))


@log_step(agent="Retrieval", node="retrieve")
def retrieve(state: dict) -> dict:
    """Fill `evidence` for the task in the state (Send payload) or the plan's first task."""
    task: Task | None = state.get("task") or (state["plan"].tasks[0] if state.get("plan") else None)
    if task is None:
        return {"evidence": {}}
    widened = bool(state.get("widened"))
    only = list(state.get("to_retry") or []) if widened and task.group != "qa" else None
    evidence = {}
    run_id = str(state.get("run_id") or "")
    for request in build_requests(task, widened, only):
        # Retrieval is the slowest node (one re-ranked search per field), so a
        # stop request is honoured between fields rather than only between
        # nodes; otherwise "Stop" could take minutes to take effect.
        if is_cancel_requested(run_id):
            raise RunCancelled(run_id)
        evidence[request.field_or_question] = run_request(request)
    return {"evidence": evidence}


def _bids(filters: SearchFilters) -> list[str]:
    """Bids a request covers: explicit ids, else every indexed bid."""
    if filters.bid_ids:
        return list(filters.bid_ids)
    if filters.bid_id:
        return [filters.bid_id]
    return get_engine().known_bids()


def _dedupe(items) -> list[str]:
    """Unique items, first occurrence wins."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
