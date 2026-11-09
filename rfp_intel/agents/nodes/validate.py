"""Validator / Critic node: rule checks, consistency checks, one LLM support check per group,
then the retry decision (BUILD_PLAN 6.4).

Checks per field: has_citation, quote_in_chunk, format_ok, consistent, supported.
A list value whose items are only partly supported is corrected (the unsupported
items are dropped and named in the notes) instead of being retried as a whole;
an item is only dropped when its content words are also absent from the field's
evidence texts, so a critic's false "unsupported" cannot delete a grounded item.
A field that fails goes to `to_retry` while its retry count is below
`agents.max_field_retries`; afterwards it is marked failed with low confidence.
Confidence = 0.4 * normalized rerank score of the best cited hit + 0.3 * passed
+ 0.2 * (>= 2 sources) + 0.1 * (no conflict); not_found = 0.8 when >= 6 passages
were checked, else 0.5.
"""

from __future__ import annotations

import json
import re

from rfp_intel import llm
from rfp_intel.agents import rules
from rfp_intel.agents.nodes.retrieve import evidence_key
from rfp_intel.agents.runtime import load_prompt
from rfp_intel.config import load_fields, settings
from rfp_intel.logging_utils import RunCancelled, log_step, record_tool_call
from rfp_intel.schemas import Chunk, EvidenceBundle, FieldValue, SupportCheck, SupportChecks, ValidationResult

FAILED_CONFIDENCE = 0.2
NOT_FOUND_HIGH, NOT_FOUND_LOW = 0.8, 0.5
NOT_FOUND_PASSAGES = 6


def chunk_index(evidence: dict[str, EvidenceBundle]) -> dict[str, Chunk]:
    """chunk_id -> Chunk over every evidence bundle."""
    return {hit.chunk.chunk_id: hit.chunk for bundle in evidence.values() for hit in bundle.hits}


def score_index(evidence: dict[str, EvidenceBundle]) -> dict[str, float]:
    """chunk_id -> min-max normalized score within its bundle (0.5 when flat)."""
    scores: dict[str, float] = {}
    for bundle in evidence.values():
        values = [h.score for h in bundle.hits]
        if not values:
            continue
        low, high = min(values), max(values)
        for hit in bundle.hits:
            scores[hit.chunk.chunk_id] = 0.5 if high == low else (hit.score - low) / (high - low)
    return scores


def rule_checks(field: str, value: FieldValue, drafts: dict[str, FieldValue],
                chunks: dict[str, Chunk], evidence: dict[str, EvidenceBundle]) -> dict[str, bool]:
    """The deterministic checks for one field."""
    texts = [h.chunk.text for key in (field, evidence_key(field, True)) for h in evidence.get(key, _EMPTY).hits]
    return {
        "has_citation": rules.has_citation(value),
        "quote_in_chunk": rules.all_quotes_in_chunks(value, chunks),
        "format_ok": rules.format_ok(field, value),
        "consistent": rules.consistent(field, drafts, texts),
    }


def support_checks(bid_id: str, group: str, fields: dict[str, FieldValue],
                   chunks: dict[str, Chunk]) -> tuple[dict[str, SupportCheck], str | None]:
    """One LLM call per group: is each ok value supported by its cited quotes?

    Returns one SupportCheck per field (a field the LLM did not mention counts
    as supported) and an error string when the call failed and was skipped.
    """
    if not fields:
        return {}, None
    fields_block = "\n".join(
        f"- {name}: value={_value_repr(v)}; notes={v.notes!r}; quotes="
        + " | ".join(f"[{c.chunk_id[-8:]}] {c.quote!r}" for c in v.sources)
        for name, v in fields.items()
    )
    cited = {c.chunk_id for v in fields.values() for c in v.sources}
    evidence_block = "\n\n".join(f"[{cid[-8:]}] ({chunks[cid].file_name}, p.{chunks[cid].page})\n{chunks[cid].text}"
                                 for cid in cited if cid in chunks)
    prompt = (load_prompt("validate").replace("{bid_id}", bid_id)
              .replace("{fields_block}", fields_block).replace("{evidence_block}", evidence_block))
    try:
        result, _ = llm.structured(prompt, SupportChecks, settings.env.reason_model)
    except RunCancelled:
        raise
    except Exception as exc:  # noqa: BLE001 - the check is skipped, not fatal
        skipped = {name: SupportCheck(field=name, supported=True, reason="support check skipped") for name in fields}
        return skipped, f"validate: support check failed for {group}: {type(exc).__name__}: {exc}"
    verdicts = {_key(item.field): item for item in result.items}
    return {name: verdicts.get(_key(name), SupportCheck(field=name, supported=True)) for name in fields}, None


def _value_repr(value: FieldValue) -> str:
    """A list value as a JSON array (one item per line entry) so the critic can name single items."""
    if isinstance(value.value, list):
        return json.dumps(value.value, ensure_ascii=False)
    return repr(rules.value_text(value))


def drop_unsupported_items(value: FieldValue, check: SupportCheck,
                           evidence_texts: list[str] | None = None) -> FieldValue | None:
    """A list value without the items the Validator found unsupported, or None.

    Only applies when some (not all) items of a list value are unsupported and
    at least one reported item matches a list item; the dropped items are
    named in the notes so the correction is visible in the record. With
    `evidence_texts`, an item the critic names is still kept when its content
    words occur in those texts (the LLM verdict alone never deletes a grounded
    item); without texts the verdict is taken as is.
    """
    if check.supported or not isinstance(value.value, list) or not check.unsupported_items:
        return None
    reported = _split_reported(check.unsupported_items, value.value)
    keep = [item for item in value.value
            if not _mentioned(item, reported) or (evidence_texts is not None and grounded(item, evidence_texts))]
    if not keep or len(keep) == len(value.value):
        return None
    dropped = [item for item in value.value if item not in keep]
    note = f"Validator removed {len(dropped)} unsupported item(s): " + "; ".join(dropped) + "."
    return value.model_copy(update={"value": keep, "notes": _join(value.notes, note)})


def _split_reported(reported: list[str], items: list[str]) -> list[str]:
    """Reported entries that are several list items joined with "; " are split back into items.

    The critic sometimes copies the whole joined value instead of single
    items; splitting keeps the item-level comparison honest in that case.
    """
    keys = {_key(i) for i in items}
    out: list[str] = []
    for text in reported:
        if _key(text) in keys or "; " not in text:
            out.append(text)
        else:
            out.extend(part for part in text.split("; ") if part.strip())
    return out


GROUNDED_SHARE = 0.6            # share of an item's content words that must occur in the evidence
_WORD = re.compile(r"[a-z0-9][a-z0-9.\-/#]*")
_GENERIC = frozenset("""the and for with from that this not are per each any all one two three minimum required
    must shall will may into than then also only such other more most upon within without including include
    including""".split())


def grounded(item: str, evidence_texts: list[str]) -> bool:
    """Do most of the item's content words occur in the evidence texts?

    Content words are tokens of three or more characters that are not generic
    function words; comparison ignores case and whitespace. An item with no
    content words is treated as grounded (nothing to check).
    """
    words = [w for w in _WORD.findall(item.lower()) if len(w) >= 3 and w not in _GENERIC]
    if not words:
        return True
    corpus = _key(" ".join(evidence_texts))
    found = sum(_key(w) in corpus for w in words)
    return found / len(words) >= GROUNDED_SHARE


def _mentioned(item: str, reported: list[str]) -> bool:
    """Does one of the reported (unsupported) items name this list item?"""
    squashed = _key(item)
    return any(_key(r) and (_key(r) in squashed or squashed in _key(r)) for r in reported)


def confidence_for(value: FieldValue, passed: bool, no_conflict: bool, scores: dict[str, float],
                   passages_checked: int) -> float:
    """The plan's confidence formula."""
    if value.value is None:
        return NOT_FOUND_HIGH if passages_checked >= NOT_FOUND_PASSAGES else NOT_FOUND_LOW
    best = max((scores.get(c.chunk_id, 0.5) for c in value.sources), default=0.0)
    score = 0.4 * best + 0.3 * passed + 0.2 * (len(value.sources) >= 2) + 0.1 * no_conflict
    return round(min(max(score, 0.0), 1.0), 3)


@log_step(agent="Validator", node="validate")
def validate(state: dict) -> dict:
    """Validate every draft; decide which fields to retry."""
    drafts: dict[str, FieldValue] = dict(state.get("drafts", {}))
    evidence: dict[str, EvidenceBundle] = state.get("evidence", {})
    chunks, scores = chunk_index(evidence), score_index(evidence)
    groups = {spec.name: spec.group for spec in load_fields()}
    bid_id = (state.get("bid_ids") or [""])[0]
    retries = dict(state.get("retries", {}))
    max_retries = settings.agents.max_field_retries
    errors: list[str] = []

    checks = {name: rule_checks(name, value, drafts, chunks, evidence) for name, value in drafts.items()}
    for group in sorted({g for g in groups.values() if g != "report"}):
        ok_fields = {n: v for n, v in drafts.items() if groups.get(n) == group and v.status == "ok"
                     and all(checks[n].values())}
        verdicts, error = support_checks(bid_id, group, ok_fields, chunks)
        if error:
            errors.append(error)
        for name in ok_fields:
            check = verdicts.get(name, SupportCheck(field=name, supported=True))
            trimmed = drop_unsupported_items(drafts[name], check, _evidence_texts(name, evidence))
            if trimmed is not None:                 # partial support: keep the supported items
                drafts[name], check = trimmed, check.model_copy(update={"supported": True})
            checks[name]["supported"] = check.supported
        if verdicts:
            record_tool_call("support_check", group=group,
                             verdicts={n: {"supported": c.supported, "reason": c.reason,
                                           "unsupported_items": c.unsupported_items} for n, c in verdicts.items()})

    validation: dict[str, ValidationResult] = {}
    updated: dict[str, FieldValue] = {}
    to_retry: list[str] = []
    for name, value in drafts.items():
        result = checks[name]
        passed = value.status != "failed" and all(result.values())
        reason = "" if passed else "failed: " + ", ".join(k for k, ok in result.items() if not ok)
        if value.status == "failed":
            reason = value.notes
        validation[name] = ValidationResult(field=name, passed=passed, checks=result, reason=reason)
        passages = sum(len(evidence[k].hits) for k in (name, evidence_key(name, True)) if k in evidence)
        if passed:
            updated[name] = value.model_copy(update={
                "confidence": confidence_for(value, True, result.get("consistent", True), scores, passages)})
        elif retries.get(name, 0) < max_retries:
            retries[name] = retries.get(name, 0) + 1
            to_retry.append(name)
            updated[name] = value
        else:
            updated[name] = value.model_copy(update={"status": "failed", "confidence": FAILED_CONFIDENCE,
                                                     "notes": _join(value.notes, f"Validation {reason}")})
    record_tool_call("validation", passed=sum(v.passed for v in validation.values()),
                     failed={n: v.reason for n, v in validation.items() if not v.passed}, to_retry=to_retry)
    return {"validation": validation, "drafts": updated, "retries": retries, "to_retry": to_retry,
            "widened": bool(to_retry), "errors": errors}


_EMPTY = EvidenceBundle(request={"field_or_question": "", "queries": [], "filters": {}}, hits=[], expanded_queries=[])  # type: ignore[arg-type]


def _evidence_texts(field: str, evidence: dict[str, EvidenceBundle]) -> list[str]:
    """Texts of every passage retrieved for a field (its own and its addendum bundle)."""
    return [h.chunk.text for key in (field, evidence_key(field, True)) for h in evidence.get(key, _EMPTY).hits]


def _key(name: str) -> str:
    return "".join(name.split()).lower()


def _join(existing: str, extra: str) -> str:
    return f"{existing} {extra}".strip() if existing and existing != "Not found in documents" else extra
