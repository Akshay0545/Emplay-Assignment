"""Report agent node: Bid Summary, BidRecord, outputs/<bid>.json and outputs/<bid>_summary.md."""

from __future__ import annotations

from pathlib import Path

from rfp_intel import llm
from rfp_intel.agents.baseline import not_found_value, value_text
from rfp_intel.agents.rules import format_datetime
from rfp_intel.agents.runtime import load_prompt
from rfp_intel.config import field_names, load_fields, settings
from rfp_intel.logging_utils import RunCancelled, log_step, now_iso
from rfp_intel.schemas import AddendumChange, BidRecord, Citation, FieldValue

SUMMARY_FIELD = "Bid Summary"
SUMMARY_CONFIDENCE = 0.6


def summarize(bid_id: str, values: dict[str, FieldValue]) -> FieldValue:
    """3-6 sentence Bid Summary from the validated (ok) fields only, with their citations."""
    ok = {n: v for n, v in values.items() if v.status == "ok" and n != SUMMARY_FIELD}
    if not ok:
        return not_found_value()
    fields_block = "\n".join(f"- {name}: {value_text(v.value)}" + (f" (note: {v.notes})" if v.notes else "")
                             for name, v in ok.items())
    prompt = load_prompt("summary").replace("{bid_id}", bid_id).replace("{fields_block}", fields_block)
    try:
        text, _ = llm.text(prompt, settings.env.reason_model)
    except RunCancelled:
        raise
    except Exception as exc:  # noqa: BLE001 - the summary must not crash the run
        return FieldValue(value=None, status="failed", confidence=0.0,
                          notes=f"LLM call failed: {type(exc).__name__}: {exc}"[:300])
    return FieldValue(value=text.strip(), sources=_union(ok.values()), confidence=SUMMARY_CONFIDENCE,
                      notes=f"Generated from {len(ok)} validated fields")


def normalize_value(value: FieldValue, how: str | None) -> FieldValue:
    """Apply a field's configured output format (fields.yaml `normalize`).

    "datetime" rewrites a validated string value as "YYYY-MM-DD HH:MM TZ" and
    keeps the documents' wording in the notes; anything that does not parse
    is left as written.
    """
    if how != "datetime" or value.status != "ok" or not isinstance(value.value, str):
        return value
    formatted = format_datetime(value.value)
    if formatted is None or formatted == value.value:
        return value
    note = f"As written: {value.value.strip().rstrip('.')}."
    return value.model_copy(update={"value": formatted, "notes": f"{note} {value.notes}".strip()})


def build_record(bid_id: str, run_id: str, drafts: dict[str, FieldValue],
                 changes: list[AddendumChange]) -> BidRecord:
    """BidRecord with exactly the configured fields (normalized) and validation counts."""
    formats = {spec.name: spec.normalize for spec in load_fields()}
    fields = {name: normalize_value(drafts.get(name, not_found_value()), formats.get(name)) for name in field_names()}
    counts = {"passed": 0, "failed": 0, "not_found": 0}
    for value in fields.values():
        counts["passed" if value.status == "ok" else value.status] += 1
    return BidRecord(bid_id=bid_id, fields=fields, addendum_changes=changes, validation=counts,
                     generated_at=now_iso(), run_id=run_id)


def write_outputs(record: BidRecord) -> Path:
    """outputs/<bid>.json and outputs/<bid>_summary.md; returns the JSON path."""
    out_dir = settings.path("outputs_dir")
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{record.bid_id}.json"
    json_path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
    (out_dir / f"{record.bid_id}_summary.md").write_text(render_summary(record), encoding="utf-8")
    return json_path


def render_summary(record: BidRecord) -> str:
    """Markdown: summary paragraph, field table, addendum changes."""
    summary = record.fields[SUMMARY_FIELD]
    lines = [f"# {record.bid_id}", "", value_text(summary.value) or "Not found in documents", "",
             f"Validation: {record.validation} | run {record.run_id} | {record.generated_at}", "",
             "| field | status | confidence | value | sources |", "|---|---|---|---|---|"]
    for name, value in record.fields.items():
        text = value_text(value.value).replace("|", "/").replace("\n", " ")
        srcs = "; ".join(f"{c.file} p.{c.page}" for c in value.sources[:3])
        lines.append(f"| {name} | {value.status} | {value.confidence:.2f} | {text[:160]} | {srcs} |")
    if record.addendum_changes:
        lines += ["", "## Addendum changes", ""]
        for change in record.addendum_changes:
            lines.append(f"- **{change.field}** ({change.kind}, Addendum {change.addendum_number}): "
                         f"{change.old_value or 'not stated'} -> {change.new_value} "
                         f"[{change.source.file} p.{change.source.page}]")
    return "\n".join(lines) + "\n"


@log_step(agent="Report", node="report")
def report(state: dict) -> dict:
    """Produce the deliverable record and write it to disk."""
    bid_id = (state.get("bid_ids") or [""])[0]
    drafts = dict(state.get("drafts", {}))
    drafts[SUMMARY_FIELD] = summarize(bid_id, drafts)
    record = build_record(bid_id, state.get("run_id", ""), drafts, list(state.get("addendum_changes", [])))
    write_outputs(record)
    return {"record": record, "drafts": {SUMMARY_FIELD: drafts[SUMMARY_FIELD]}}


def _union(values) -> list[Citation]:
    seen: set[tuple[str, str]] = set()
    out: list[Citation] = []
    for value in values:
        for citation in value.sources:
            if (citation.chunk_id, citation.quote) not in seen:
                seen.add((citation.chunk_id, citation.quote))
                out.append(citation)
    return out
