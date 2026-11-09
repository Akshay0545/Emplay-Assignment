"""Q&A agent node (answer) and its validator (validate_answer).

Answers come only from the evidence the Retrieval agent collected (keys
"qa::<bid>"), with citations, or are "Not found in documents". Change
questions also see the addendum change log of the saved BidRecord(s).
"""

from __future__ import annotations

import json

from rfp_intel import llm
from rfp_intel.agents import rules
from rfp_intel.agents.baseline import find_span
from rfp_intel.agents.nodes.retrieve import QA_PREFIX
from rfp_intel.agents.nodes.validate import chunk_index, score_index
from rfp_intel.agents.runtime import load_prompt
from rfp_intel.config import settings
from rfp_intel.logging_utils import RunCancelled, log_step, record_tool_call
from rfp_intel.schemas import Answer, AnswerDraft, BidRecord, Citation, EvidenceBundle, SearchHit, SupportChecks

NOT_FOUND = "Not found in documents"
QUOTE_LIMIT = 300
MAX_ANSWER_RETRIES = 1
ANSWER_CHECK_RULES = (
    "\n\nThe field above is a free-text answer to a user's question. Judge only its factual claims "
    "(dates, times, numbers, names, amounts, requirements): each must be stated in the cited evidence. "
    "Wording that merely restates the question's framing (\"after all addendums\", \"for each bid\", "
    "\"the final\", \"currently\") needs no separate proof. An answer that says a bid lacks "
    "information is supported when the evidence for that bid indeed does not contain it.\n"
)


def qa_evidence(evidence: dict[str, EvidenceBundle]) -> dict[str, SearchHit]:
    """Numbered hits across the qa bundles, grouped by bid (E1, E2, ...)."""
    numbered: dict[str, SearchHit] = {}
    seen: set[str] = set()
    for key in sorted(k for k in evidence if k.startswith(QA_PREFIX)):
        for hit in evidence[key].hits:
            chunk_key = f"{hit.chunk.bid_id}/{hit.chunk.chunk_id}"
            if chunk_key not in seen:
                seen.add(chunk_key)
                numbered[f"E{len(numbered) + 1}"] = hit
    return numbered


def change_log_block(bid_ids: list[str]) -> str:
    """Addendum change log lines from saved outputs/<bid>.json files (empty when none)."""
    lines: list[str] = []
    for bid_id in bid_ids:
        path = settings.path("outputs_dir") / f"{bid_id}.json"
        if not path.exists():
            continue
        try:
            record = BidRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except (ValueError, json.JSONDecodeError):
            continue
        for change in record.addendum_changes:
            lines.append(f"- {bid_id} / {change.field}: {change.kind} by Addendum {change.addendum_number}: "
                         f"{change.old_value or 'not stated'} -> {change.new_value} "
                         f"[{change.source.file} p.{change.source.page}]")
    return "Addendum change log from a previous extraction run:\n" + "\n".join(lines) + "\n" if lines else ""


def build_prompt(state: dict, evidence: dict[str, SearchHit]) -> str:
    """Fill answer.md."""
    plan = state.get("plan")
    bids = list(state.get("bid_ids") or [])
    question_type = plan.question_type if plan else "single"
    by_bid: dict[str, list[str]] = {}
    for eid, hit in evidence.items():
        by_bid.setdefault(hit.chunk.bid_id, []).append(
            f"[{eid}] ({hit.chunk.file_name}, p.{hit.chunk.page}) {hit.chunk.header}\n{hit.chunk.text}")
    evidence_block = "\n\n".join(f"## {bid}\n" + "\n\n".join(parts) for bid, parts in by_bid.items()) or "(no evidence found)"
    change_log = change_log_block(bids) if question_type == "changes" else ""
    prompt = (load_prompt("answer").replace("{question}", state.get("question") or "")
              .replace("{question_type}", question_type).replace("{bid_ids}", ", ".join(bids) or "all")
              .replace("{change_log_block}", change_log).replace("{evidence_block}", evidence_block))
    feedback = state.get("answer_feedback")
    if feedback:
        prompt += (f"\n\nYour previous draft was rejected by the validator: {feedback}\n"
                   "Redraft it. State only facts that appear in the evidence passages above; drop any "
                   "claim the evidence does not support, and cite the ids that support what remains.")
    return prompt


def resolve_citations(draft: AnswerDraft, evidence: dict[str, SearchHit]) -> list[Citation]:
    """Evidence ids -> Citations with verified quotes; unknown ids dropped."""
    citations: list[Citation] = []
    for index, raw in enumerate(draft.evidence_ids):
        hit = evidence.get(_norm_id(raw))
        if hit is None:
            continue
        quote = draft.quotes[index] if index < len(draft.quotes) else ""
        span = find_span(hit.chunk.text, quote) if quote else None
        citations.append(Citation(file=hit.chunk.file_name, page=hit.chunk.page, chunk_id=hit.chunk.chunk_id,
                                  quote=(span or hit.chunk.text[:QUOTE_LIMIT])[:QUOTE_LIMIT]))
    return citations


def not_found_answer(question: str, bids: list[str], passages: int) -> Answer:
    """The guardrail answer."""
    return Answer(question=question, answer=NOT_FOUND, citations=[], confidence=0.8 if passages >= 6 else 0.5, bids=bids)


@log_step(agent="Q&A", node="answer")
def answer(state: dict) -> dict:
    """Draft a cited answer from the qa evidence."""
    question = state.get("question") or ""
    bids = list(state.get("bid_ids") or [])
    evidence = qa_evidence(state.get("evidence", {}))
    if not evidence:
        return {"answer": not_found_answer(question, bids, 0)}
    try:
        draft, _ = llm.structured(build_prompt(state, evidence), AnswerDraft, settings.env.reason_model)
    except RunCancelled:
        raise
    except Exception as exc:  # noqa: BLE001 - answer not_found rather than crash
        return {"answer": not_found_answer(question, bids, len(evidence)),
                "errors": [f"answer: LLM call failed: {type(exc).__name__}: {exc}"]}
    citations = resolve_citations(draft, evidence)
    record_tool_call("draft", found=draft.found, answer=draft.answer[:300], evidence_ids=draft.evidence_ids,
                     resolved_citations=len(citations))
    if not draft.found or not citations or draft.answer.strip().lower().startswith(NOT_FOUND.lower()):
        return {"answer": not_found_answer(question, bids, len(evidence))}
    scores = score_index(state.get("evidence", {}))
    best = max(scores.get(c.chunk_id, 0.5) for c in citations)
    confidence = round(min(1.0, 0.4 * best + 0.2 * (len(citations) >= 2) + 0.1), 3)
    bids_cited = sorted({evidence[_norm_id(r)].chunk.bid_id for r in draft.evidence_ids if _norm_id(r) in evidence})
    return {"answer": Answer(question=question, answer=draft.answer.strip(), citations=citations,
                             confidence=confidence, bids=bids_cited or bids)}


@log_step(agent="Validator", node="validate_answer")
def validate_answer(state: dict) -> dict:
    """Check quotes and support; request one redraft (with feedback) when unsupported."""
    ans: Answer | None = state.get("answer")
    if ans is None or ans.answer == NOT_FOUND:
        return {"to_retry": [], "widened": False}
    chunks = chunk_index(state.get("evidence", {}))
    quotes_ok = all(rules.quote_in_chunk(c, chunks) for c in ans.citations)
    supported, reason, error = _support(ans, chunks)
    errors = [error] if error else []
    record_tool_call("answer_check", quotes_ok=quotes_ok, supported=supported, reason=reason,
                     citations=len(ans.citations), retries=dict(state.get("retries", {})))
    if quotes_ok and supported:
        return {"answer": ans.model_copy(update={"confidence": round(min(1.0, ans.confidence + 0.3), 3)}),
                "to_retry": [], "widened": False, "errors": errors}
    retries = dict(state.get("retries", {}))
    if retries.get("answer", 0) < MAX_ANSWER_RETRIES:
        retries["answer"] = retries.get("answer", 0) + 1
        feedback = reason or ("a cited quote is not in its passage" if not quotes_ok else "unsupported claim")
        return {"retries": retries, "to_retry": ["answer"], "widened": False, "answer_feedback": feedback,
                "errors": errors}
    passages = sum(len(b.hits) for k, b in state.get("evidence", {}).items() if k.startswith(QA_PREFIX))
    return {"answer": not_found_answer(ans.question, ans.bids, passages), "to_retry": [], "widened": False,
            "errors": errors}


def _support(ans: Answer, chunks: dict) -> tuple[bool, str, str | None]:
    """One LLM support check for the whole answer: (supported, reason, error)."""
    fields_block = f"- answer: value={ans.answer!r}; quotes=" + " | ".join(f"{c.quote!r}" for c in ans.citations)
    cited = {c.chunk_id for c in ans.citations}
    evidence_block = "\n\n".join(f"[{cid[-8:]}] ({chunks[cid].file_name}, p.{chunks[cid].page})\n{chunks[cid].text}"
                                 for cid in cited if cid in chunks)
    prompt = (load_prompt("validate").replace("{bid_id}", ", ".join(ans.bids))
              .replace("{fields_block}", fields_block).replace("{evidence_block}", evidence_block)
              + ANSWER_CHECK_RULES)
    try:
        result, _ = llm.structured(prompt, SupportChecks, settings.env.reason_model)
    except RunCancelled:
        raise
    except Exception as exc:  # noqa: BLE001
        return True, "", f"validate_answer: support check failed: {type(exc).__name__}: {exc}"
    if not result.items:
        return True, "", None
    return all(item.supported for item in result.items), "; ".join(i.reason for i in result.items if i.reason), None


def _norm_id(raw: str | None) -> str:
    digits = "".join(ch for ch in (raw or "") if ch.isdigit())
    return f"E{digits}" if digits else ""
