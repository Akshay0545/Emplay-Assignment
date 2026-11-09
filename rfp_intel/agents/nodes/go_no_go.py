"""Go/No-Go agent node (bonus): recommendation against config/capabilities.yaml.

Reads the saved BidRecord, asks the LLM for one met / not_met / unknown
verdict per criterion using only the fields the criterion names, then applies
deterministic rules: a criterion whose fields are all missing gets its
configured `when_not_found` status without asking the LLM; the recommendation
is no_go when a critical criterion is not met, review when something is
unknown or a non-critical criterion is not met, and go otherwise. Citations of
the fields used are attached to each criterion. An LLM failure marks the
undecided criteria unknown and records the error; it never fails the run.
"""

from __future__ import annotations

import json

from rfp_intel import llm
from rfp_intel.agents.baseline import value_text
from rfp_intel.agents.nodes.compare import NOT_FOUND
from rfp_intel.agents.runtime import load_prompt
from rfp_intel.config import Capabilities, Criterion, load_capabilities, settings
from rfp_intel.logging_utils import RunCancelled, log_step, now_iso, record_tool_call
from rfp_intel.schemas import BidRecord, Citation, CriterionChecks, CriterionResult, GoNoGoReport

LABELS = {"go": "Go", "no_go": "No-go", "review": "Needs review"}
VALID_STATUSES = {"met", "not_met", "unknown"}


def load_record(bid_id: str) -> BidRecord:
    """The saved record; FileNotFoundError with the command to produce it."""
    path = settings.path("outputs_dir") / f"{bid_id}.json"
    if not path.is_file():
        raise FileNotFoundError(f"no extraction record for {bid_id!r}; run: python main.py --bid data/{bid_id}")
    return BidRecord.model_validate_json(path.read_text(encoding="utf-8"))


def _has_value(record: BidRecord, name: str) -> bool:
    """Whether the record states a value for the field."""
    field = record.fields.get(name)
    return field is not None and field.value is not None


def _sources(record: BidRecord, names: list[str]) -> list[Citation]:
    """Union of the citations of the named fields."""
    seen: set[tuple[str, str]] = set()
    out: list[Citation] = []
    for name in names:
        field = record.fields.get(name)
        for citation in (field.sources if field else []):
            if (citation.chunk_id, citation.quote) not in seen:
                seen.add((citation.chunk_id, citation.quote))
                out.append(citation)
    return out


def _fields_block(record: BidRecord, names: list[str]) -> str:
    """Field values as prompt lines."""
    lines = []
    for name in names:
        field = record.fields.get(name)
        text = value_text(field.value) if field and field.value is not None else NOT_FOUND
        note = f" (note: {field.notes})" if field and field.value is not None and field.notes else ""
        lines.append(f"- {name}: {text}{note}")
    return "\n".join(lines)


def _criteria_block(criteria: list[Criterion]) -> str:
    """Criteria as prompt lines: id, label, fields, capability, rule."""
    return "\n".join(
        f"- id: {c.id} | {c.label} | fields: {', '.join(c.fields)} | capability: {json.dumps(c.capability)}"
        f"\n  rule: {c.rule.strip()}" for c in criteria)


def judge(record: BidRecord, caps: Capabilities) -> tuple[list[CriterionResult], str | None]:
    """All criterion results: rule-decided ones first, the rest from one LLM call."""
    decided: dict[str, CriterionResult] = {}
    to_ask: list[Criterion] = []
    for crit in caps.criteria:
        if not any(_has_value(record, name) for name in crit.fields):
            reason = f"None of the fields {', '.join(crit.fields)} is stated in the documents"
            decided[crit.id] = _result(crit, crit.when_not_found, reason, record)
        else:
            to_ask.append(crit)
    verdicts, error = _ask(record, caps, to_ask) if to_ask else ({}, None)
    results = []
    for crit in caps.criteria:
        if crit.id in decided:
            results.append(decided[crit.id])
            continue
        status, reason = verdicts.get(crit.id, ("unknown", error or "the LLM returned no verdict for this criterion"))
        results.append(_result(crit, status, reason, record))
    record_tool_call("verdicts", bid_id=record.bid_id, asked=[c.id for c in to_ask], rule_decided=list(decided),
                     statuses={r.criterion: r.status for r in results})
    return results, error


def _ask(record: BidRecord, caps: Capabilities, criteria: list[Criterion]) -> tuple[dict[str, tuple[str, str]], str | None]:
    """One structured LLM call -> {criterion id: (status, reason)}, error."""
    order = list(record.fields)
    used = sorted({name for c in criteria for name in c.fields}, key=lambda n: order.index(n) if n in order else 99)
    prompt = (load_prompt("go_no_go").replace("{company}", json.dumps(caps.company))
              .replace("{bid_id}", record.bid_id).replace("{fields_block}", _fields_block(record, used))
              .replace("{criteria_block}", _criteria_block(criteria)))
    try:
        checks, _ = llm.structured(prompt, CriterionChecks, settings.env.reason_model)
    except RunCancelled:
        raise
    except Exception as exc:  # noqa: BLE001 - undecided criteria become unknown
        return {}, f"go_no_go: LLM call failed: {type(exc).__name__}: {exc}"
    return {item.criterion.strip(): (item.status, item.reason.strip()) for item in checks.items
            if item.status in VALID_STATUSES}, None


def _result(crit: Criterion, status: str, reason: str, record: BidRecord) -> CriterionResult:
    """A CriterionResult with the citations of the criterion's fields."""
    return CriterionResult(criterion=crit.id, label=crit.label, status=status, critical=crit.critical,  # type: ignore[arg-type]
                           reason=reason[:500], fields=list(crit.fields), sources=_sources(record, crit.fields))


def recommend(results: list[CriterionResult], caps: Capabilities) -> str:
    """no_go / review / go from the criterion statuses (deterministic)."""
    if any(r.critical and r.status == "not_met" for r in results):
        return "no_go"
    if caps.decision.unknown_critical == "no_go" and any(r.critical and r.status == "unknown" for r in results):
        return "no_go"
    if any(r.status != "met" for r in results):
        return "review"
    return "go"


def summary_text(recommendation: str, results: list[CriterionResult]) -> str:
    """One paragraph naming what drove the recommendation."""
    not_met = [r.label for r in results if r.status == "not_met"]
    unknown = [r.label for r in results if r.status == "unknown"]
    met = sum(r.status == "met" for r in results)
    parts = [f"{LABELS[recommendation]}: {met} of {len(results)} criteria met."]
    if not_met:
        parts.append("Not met: " + "; ".join(not_met) + ".")
    if unknown:
        parts.append("Unknown (not stated in the documents or undecidable): " + "; ".join(unknown) + ".")
    return " ".join(parts)


def render_markdown(report: GoNoGoReport) -> str:
    """outputs/<bid>_go_no_go.md."""
    lines = [f"# {report.bid_id}: {LABELS[report.recommendation]}", "", report.summary, "",
             f"Generated {report.generated_at} | run {report.run_id} | profile config/capabilities.yaml", "",
             "| criterion | status | critical | reason | fields | sources |", "|---|---|---|---|---|---|"]
    for r in report.criteria:
        srcs = "; ".join(f"{c.file} p.{c.page}" for c in r.sources[:3])
        lines.append(f"| {r.label} | {r.status.replace('_', ' ')} | {'yes' if r.critical else ''} | "
                     f"{r.reason.replace('|', '/')} | {', '.join(r.fields)} | {srcs} |")
    return "\n".join(lines) + "\n"


def write_outputs(report: GoNoGoReport) -> None:
    """outputs/<bid>_go_no_go.json and .md."""
    out_dir = settings.path("outputs_dir")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{report.bid_id}_go_no_go.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    (out_dir / f"{report.bid_id}_go_no_go.md").write_text(render_markdown(report), encoding="utf-8")


@log_step(agent="Go/No-Go", node="go_no_go")
def go_no_go(state: dict) -> dict:
    """Judge state["bid_ids"][0] against the capability profile and write the report."""
    bid_id = (state.get("bid_ids") or [""])[0]
    record = load_record(bid_id)
    caps = load_capabilities()
    results, error = judge(record, caps)
    recommendation = recommend(results, caps)
    report = GoNoGoReport(
        bid_id=bid_id, recommendation=recommendation, criteria=results,  # type: ignore[arg-type]
        summary=summary_text(recommendation, results),
        met=sum(r.status == "met" for r in results), not_met=sum(r.status == "not_met" for r in results),
        unknown=sum(r.status == "unknown" for r in results), generated_at=now_iso(), run_id=state.get("run_id", ""))
    write_outputs(report)
    return {"go_no_go": report, "errors": [error] if error else []}
