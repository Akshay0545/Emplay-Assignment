"""Addendum Reconciler node: apply addendum overrides, clarifications and additions.

One LLM call (REASON_MODEL) judges every field that has addendum evidence.
Decisions are applied in addendum order so the latest addendum wins
(`precedence` in settings.yaml puts addendum first). The old value and its
source go into the field's notes and an AddendumChange is recorded.
"""

from __future__ import annotations

import re

from rfp_intel import llm
from rfp_intel.agents.baseline import find_span, value_text
from rfp_intel.agents.nodes.retrieve import evidence_key
from rfp_intel.agents.runtime import load_prompt
from rfp_intel.config import load_fields, settings
from rfp_intel.logging_utils import RunCancelled, log_step
from rfp_intel.schemas import (
    AddendumChange,
    Citation,
    EvidenceBundle,
    FieldValue,
    ReconcileDecision,
    ReconcileDecisions,
    SearchHit,
)

QUOTE_LIMIT = 300


def addendum_evidence(state: dict, fields: list[str]) -> tuple[dict[str, SearchHit], dict[str, list[str]]]:
    """Numbered addendum hits (E1, E2, ...) and, per field, the ids that came from its request."""
    evidence: dict[str, EvidenceBundle] = state.get("evidence", {})
    numbered: dict[str, SearchHit] = {}
    ids_by_chunk: dict[str, str] = {}
    per_field: dict[str, list[str]] = {}
    for field in fields:
        bundle = evidence.get(evidence_key(field, addendum=True))
        for hit in (bundle.hits if bundle else []):
            key = f"{hit.chunk.bid_id}/{hit.chunk.chunk_id}"
            if key not in ids_by_chunk:
                ids_by_chunk[key] = f"E{len(numbered) + 1}"
                numbered[ids_by_chunk[key]] = hit
            per_field.setdefault(field, []).append(ids_by_chunk[key])
    return numbered, per_field


def build_prompt(bid_id: str, drafts: dict[str, FieldValue], fields: list[str],
                 evidence: dict[str, SearchHit]) -> str:
    """Fill reconcile.md."""
    descriptions = {spec.name: spec.description for spec in load_fields()}
    fields_block = "\n".join(
        f"- {name}: {descriptions.get(name, '')} | {value_text(drafts[name].value) or 'null'} | "
        f"{drafts[name].notes or ''}"
        for name in fields
    )
    evidence_block = "\n\n".join(
        f"[{eid}] ({hit.chunk.file_name}, p.{hit.chunk.page}) {hit.chunk.header}\n{hit.chunk.text}"
        for eid, hit in evidence.items()
    )
    return (load_prompt("reconcile").replace("{bid_id}", bid_id)
            .replace("{fields_block}", fields_block).replace("{evidence_block}", evidence_block))


def apply_decision(field: str, draft: FieldValue, decision: ReconcileDecision,
                   evidence: dict[str, SearchHit]) -> tuple[FieldValue, AddendumChange | None]:
    """Apply one decision to a draft; returns the new value and the change (or None)."""
    hit = evidence.get(_norm_id(decision.evidence_id))
    if decision.kind == "none" or hit is None or not decision.new_value:
        return draft, None
    current = value_text(draft.value)
    if decision.kind == "clarification" and _same(decision.new_value, current):
        return draft, None                 # nothing new: the extractor already had the addendum value
    chunk = hit.chunk
    span = find_span(chunk.text, decision.quote or "") or chunk.text[:QUOTE_LIMIT]
    citation = Citation(file=chunk.file_name, page=chunk.page, chunk_id=chunk.chunk_id, quote=span[:QUOTE_LIMIT])
    number = chunk.addendum_number or 0
    old_text = _old_value(decision, current, draft.notes)
    change = AddendumChange(field=field, kind=decision.kind, old_value=old_text, new_value=decision.new_value,
                            addendum_number=number, source=citation)
    origin = f" ({draft.sources[0].file} p.{draft.sources[0].page})" if draft.sources else ""
    if decision.kind == "override":
        note = f"Changed by Addendum {number}: originally {old_text or 'not stated'}{origin}."
        value = FieldValue(value=decision.new_value, sources=[citation], confidence=draft.confidence,
                           notes=_join_notes(note, draft.notes), status="ok")
    elif decision.kind == "addition":
        combined = _add_value(draft.value, decision.new_value)
        note = f"Addendum {number} adds: {decision.new_value.rstrip('.')}."
        value = FieldValue(value=combined, sources=draft.sources + [citation], confidence=draft.confidence,
                           notes=_join_notes(note, draft.notes if draft.value is not None else ""), status="ok")
    else:  # clarification
        note = f"Clarified by Addendum {number}: {decision.new_value.rstrip('.')}."
        if draft.value is None:
            value = FieldValue(value=decision.new_value, sources=[citation], confidence=draft.confidence,
                               notes=note, status="ok")
        else:
            value = draft.model_copy(update={"sources": draft.sources + [citation],
                                             "notes": _join_notes(note, draft.notes)})
    return value, change


@log_step(agent="Addendum Reconciler", node="reconcile")
def reconcile(state: dict) -> dict:
    """Reconcile every draft (or only the retried ones) against addendum evidence."""
    drafts: dict[str, FieldValue] = dict(state.get("drafts", {}))
    fields = [f for f in (state.get("to_retry") if state.get("widened") else None) or drafts if f in drafts]
    evidence, per_field = addendum_evidence(state, fields)
    with_evidence = [f for f in fields if per_field.get(f)]
    if not with_evidence:
        return {}
    bid_id = (state.get("bid_ids") or [""])[0]
    try:
        decisions, _ = llm.structured(build_prompt(bid_id, drafts, with_evidence, evidence),
                                      ReconcileDecisions, settings.env.reason_model)
    except RunCancelled:
        raise
    except Exception as exc:  # noqa: BLE001 - keep the drafts as they are
        return {"errors": [f"reconcile: LLM call failed: {type(exc).__name__}: {exc}"]}

    changes = list(state.get("addendum_changes", []))
    updated: dict[str, FieldValue] = {}
    ordered = sorted(decisions.items, key=lambda d: _addendum_number(d, evidence))
    for decision in ordered:
        field = _match_field(decision.field, with_evidence)
        if field is None:
            continue
        value, change = apply_decision(field, updated.get(field, drafts[field]), decision, evidence)
        if change is not None:
            updated[field] = value
            changes = [c for c in changes if not _same_change(c, change)] + [change]   # a rerun replaces its earlier entry
    return {"drafts": updated, "addendum_changes": changes}


_ORIGINAL_NOTE = re.compile(
    r"\b(?:original|previous|prior|earlier)\b[^.]{0,40}?\b(?:was|were)\s+(?P<value>[^;]+?)\.?(?:\s*$|;|\.\s)",
    re.IGNORECASE,
)


def _old_value(decision: ReconcileDecision, current: str | None, notes: str) -> str | None:
    """The value the addendum replaced; never the new value itself.

    The LLM sometimes echoes the new value as old_value when the extractor already
    saw the addendum. Order: LLM old_value (if different), the current draft (if
    different), an "original ... was X" statement in the draft notes, else None.
    """
    new = decision.new_value
    for candidate in (decision.old_value, current):
        if candidate and not _same(candidate, new) and not _same_core(candidate, new):
            return candidate
    for match in _ORIGINAL_NOTE.finditer(notes or ""):
        value = match.group("value").strip().rstrip(".")
        if value and not _same_core(value, new):
            return value
    return None


def _same_core(a: str | None, b: str | None) -> bool:
    """Equal after dropping punctuation and case ("July 9, 2024." == "july 9 2024")."""
    strip = lambda s: "".join(ch for ch in (s or "").lower() if ch.isalnum())  # noqa: E731
    return bool(strip(a)) and strip(a) == strip(b)


def _addendum_number(decision: ReconcileDecision, evidence: dict[str, SearchHit]) -> int:
    hit = evidence.get(_norm_id(decision.evidence_id))
    return (hit.chunk.addendum_number or 0) if hit else 0


def _norm_id(raw: str | None) -> str:
    digits = "".join(ch for ch in (raw or "") if ch.isdigit())
    return f"E{digits}" if digits else ""


def _match_field(name: str, fields: list[str]) -> str | None:
    key = "".join(name.split()).lower()
    return next((f for f in fields if "".join(f.split()).lower() == key), None)


def _same_change(a: AddendumChange, b: AddendumChange) -> bool:
    return a.field == b.field and a.kind == b.kind and a.addendum_number == b.addendum_number


def _same(a: str | None, b: str | None) -> bool:
    return "".join((a or "").split()).lower() == "".join((b or "").split()).lower()


def _add_value(current: str | list[str] | None, new: str) -> str | list[str]:
    if current is None:
        return new
    items = current if isinstance(current, list) else [current]
    return items if new in items else items + [new]


def _join_notes(note: str, existing: str) -> str:
    """Prepend the addendum note to the draft's notes without repeating either."""
    if not existing or existing == "Not found in documents":
        return note
    if _squash(existing) in _squash(note):
        return note
    if _squash(note) in _squash(existing):
        return existing
    return f"{note} {existing}".strip()


def _squash(text: str) -> str:
    return "".join(text.split()).lower().rstrip(".")
