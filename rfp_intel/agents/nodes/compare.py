"""Comparison agent node (bonus): side-by-side report of two or more bids.

Works from the saved BidRecords (outputs/<bid>.json): the rows are built in
code (one per configured field, one cell per bid, `differs` when the values
are not equivalent), and one LLM call writes the narrative and the highlights
from those validated values only. Every highlight carries the citations of
the cells it talks about. An LLM failure leaves the rows in place and records
the error; it never fails the run.
"""

from __future__ import annotations

import re

from rfp_intel import llm
from rfp_intel.agents.baseline import value_text
from rfp_intel.agents.runtime import load_prompt
from rfp_intel.config import field_names, settings
from rfp_intel.logging_utils import RunCancelled, log_step, now_iso, record_tool_call
from rfp_intel.schemas import (
    BidRecord,
    Citation,
    ComparisonCell,
    ComparisonDraft,
    ComparisonHighlight,
    ComparisonReport,
    ComparisonRow,
    FieldValue,
)

NOT_FOUND = "Not found in documents"
NARRATIVE_UNAVAILABLE = "Narrative unavailable: the LLM call failed; the table below is complete."


def load_records(bid_ids: list[str]) -> dict[str, BidRecord]:
    """Saved records for the bids; FileNotFoundError names the first bid without one."""
    records: dict[str, BidRecord] = {}
    for bid_id in bid_ids:
        path = settings.path("outputs_dir") / f"{bid_id}.json"
        if not path.is_file():
            raise FileNotFoundError(
                f"no extraction record for {bid_id!r}; run: python main.py --bid data/{bid_id}"
            )
        records[bid_id] = BidRecord.model_validate_json(path.read_text(encoding="utf-8"))
    return records


def build_rows(records: dict[str, BidRecord]) -> list[ComparisonRow]:
    """One row per configured field with a cell per bid."""
    rows: list[ComparisonRow] = []
    for name in field_names():
        cells = [_cell(bid_id, record.fields.get(name)) for bid_id, record in records.items()]
        rows.append(ComparisonRow(field=name, cells=cells, differs=_differs(cells)))
    return rows


def _cell(bid_id: str, value: FieldValue | None) -> ComparisonCell:
    """A cell from a FieldValue (a missing field is shown as not found)."""
    if value is None:
        return ComparisonCell(bid_id=bid_id, value=None, status="not_found", confidence=0.0, notes=NOT_FOUND)
    return ComparisonCell(bid_id=bid_id, value=value.value, status=value.status, confidence=value.confidence,
                          notes=value.notes, sources=list(value.sources))


def _differs(cells: list[ComparisonCell]) -> bool:
    """True unless every cell has the same normalized value (all-missing counts as equal)."""
    normalized = {_norm(value_text(c.value)) if c.value is not None else "" for c in cells}
    return len(normalized) > 1


def _norm(text: str) -> str:
    """Case/punctuation-insensitive form for equality."""
    return re.sub(r"[\s.,;:]+", " ", text).strip().lower()


def fields_block(rows: list[ComparisonRow]) -> str:
    """The values table as prompt text, skipping fields no bid has."""
    lines: list[str] = []
    for row in rows:
        if all(c.value is None for c in row.cells):
            continue
        lines.append(f"- {row.field}:")
        for cell in row.cells:
            text = value_text(cell.value) if cell.value is not None else NOT_FOUND
            note = f" (note: {cell.notes})" if cell.value is not None and cell.notes else ""
            lines.append(f"    {cell.bid_id}: {text}{note}")
    return "\n".join(lines) or "(no values extracted)"


def write_narrative(bid_ids: list[str], rows: list[ComparisonRow]) -> tuple[str, list[ComparisonHighlight], str | None]:
    """One LLM call -> (narrative, highlights, error)."""
    prompt = (load_prompt("compare").replace("{bid_ids}", ", ".join(bid_ids))
              .replace("{fields_block}", fields_block(rows)))
    try:
        draft, _ = llm.structured(prompt, ComparisonDraft, settings.env.reason_model)
    except RunCancelled:
        raise
    except Exception as exc:  # noqa: BLE001 - the table is still a valid report
        return NARRATIVE_UNAVAILABLE, [], f"compare: LLM call failed: {type(exc).__name__}: {exc}"
    by_field = {row.field: row for row in rows}
    highlights = [ComparisonHighlight(field=h.field, note=h.note.strip(), sources=_row_sources(by_field[h.field]))
                  for h in draft.highlights if h.field in by_field and h.note.strip()]
    record_tool_call("draft", highlights=len(draft.highlights), kept=len(highlights))
    return draft.narrative.strip(), highlights, None


def _row_sources(row: ComparisonRow) -> list[Citation]:
    """Union of the citations of a row's cells."""
    seen: set[tuple[str, str]] = set()
    out: list[Citation] = []
    for cell in row.cells:
        for citation in cell.sources:
            if (citation.chunk_id, citation.quote) not in seen:
                seen.add((citation.chunk_id, citation.quote))
                out.append(citation)
    return out


def render_markdown(report: ComparisonReport) -> str:
    """outputs/comparison.md."""
    lines = [f"# Bid comparison: {' vs '.join(report.bid_ids)}", "", report.narrative, "",
             f"Generated {report.generated_at} | run {report.run_id}", ""]
    if report.highlights:
        lines += ["## Highlights", ""]
        for h in report.highlights:
            srcs = "; ".join(f"{c.file} p.{c.page}" for c in h.sources[:4])
            lines.append(f"- **{h.field}**: {h.note}" + (f" [{srcs}]" if srcs else ""))
        lines.append("")
    lines += ["## Side by side", "", "| field | " + " | ".join(report.bid_ids) + " | differs |",
              "|---|" + "---|" * len(report.bid_ids) + "---|"]
    for row in report.rows:
        cells = []
        for cell in row.cells:
            text = (value_text(cell.value) if cell.value is not None else NOT_FOUND)
            text = text.replace("|", "/").replace("\n", " ")
            srcs = "; ".join(f"{c.file} p.{c.page}" for c in cell.sources[:2])
            cells.append(text[:200] + (f" ({srcs})" if srcs else ""))
        lines.append(f"| {row.field} | " + " | ".join(cells) + f" | {'yes' if row.differs else ''} |")
    return "\n".join(lines) + "\n"


def write_outputs(report: ComparisonReport) -> None:
    """outputs/comparison.json and outputs/comparison.md (the latest comparison)."""
    out_dir = settings.path("outputs_dir")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "comparison.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    (out_dir / "comparison.md").write_text(render_markdown(report), encoding="utf-8")


@log_step(agent="Comparison", node="compare")
def compare(state: dict) -> dict:
    """Build the comparison report for state["bid_ids"] and write it to disk."""
    bid_ids = list(state.get("bid_ids") or [])
    records = load_records(bid_ids)
    rows = build_rows(records)
    narrative, highlights, error = write_narrative(bid_ids, rows)
    report = ComparisonReport(bid_ids=bid_ids, rows=rows, narrative=narrative, highlights=highlights,
                              generated_at=now_iso(), run_id=state.get("run_id", ""))
    write_outputs(report)
    record_tool_call("report", bid_ids=list(bid_ids), rows=len(rows), differing=sum(r.differs for r in rows))
    return {"comparison": report, "errors": [error] if error else []}
