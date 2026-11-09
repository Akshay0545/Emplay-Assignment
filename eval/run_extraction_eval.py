"""Field-level extraction accuracy against eval/gold/<bid>.json (BUILD_PLAN 7.2).

    python -m eval.run_extraction_eval [--bid Bid1 ...] [--no-judge]

For every bid that has both a gold file and outputs/<bid>.json each field is
scored exact / partial / wrong: `must_contain` tokens are checked strictly;
fields without tokens (and the faithfulness-scored Bid Summary) go to an LLM
judge. Results: eval/results/extraction.md and extraction.json.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from rfp_intel import llm
from rfp_intel.config import settings
from rfp_intel.schemas import BidRecord, FieldValue
from eval.loaders import EVAL_DIR, GoldField, contains_phrase, gold_bid_ids, load_gold

RESULTS_DIR = EVAL_DIR / "results"
Score = Literal["exact", "partial", "wrong"]

JUDGE_PROMPT = (
    "You compare an extracted RFP field value with the gold (reference) value.\n"
    "Field: {field}\nGold value: {gold}\nExtracted value: {output}\n\n"
    "Verdict rules: \"exact\" if the extracted value states the same facts as the gold "
    "(wording and format may differ; extra correct detail is fine); \"partial\" if it "
    "states some of the gold facts, or is correct but clearly incomplete; \"wrong\" if it "
    "contradicts the gold or misses its substance. Reply with the verdict and a one-sentence reason."
)


class JudgeVerdict(BaseModel):
    """LLM judge output for one field."""

    verdict: Score
    reason: str = ""


class FieldScore(BaseModel):
    """Score of one field of one bid."""

    field: str
    gold: str
    output: str
    score: Score
    citations: int
    counted: bool                        # False for faithfulness-scored fields
    reason: str = ""


class BidResult(BaseModel):
    """All field scores of one bid plus summary numbers."""

    bid_id: str
    fields: list[FieldScore]
    exact: int
    partial: int
    wrong: int
    scored_fields: int
    accuracy: float
    fields_with_citations: int
    values_without_citation: int


# ---------------------------------------------------------------- scoring


def score_field(field: str, gold: GoldField, output: FieldValue, judge: bool = True,
                reference: str | None = None) -> FieldScore:
    """Score one field: strict tokens, else LLM judge (or partial when judge is off).

    `reference` replaces the gold value as the judge's comparison text (used for
    faithfulness-scored fields whose gold value is null).
    """
    out_text = value_text(output.value)
    has_value = output.value is not None and output.status != "not_found" and out_text != ""
    base = {"field": field, "gold": value_text(gold.value), "output": out_text,
            "citations": len(output.sources), "counted": gold.scoring != "faithfulness"}
    gold_none = gold.status == "not_found" or _is_none_phrase(value_text(gold.value))
    if gold_none:
        absent = not has_value or _is_none_phrase(out_text)
        score: Score = "exact" if absent else "wrong"
        return FieldScore(**base, score=score, reason="gold states nothing is required / not found")
    if not has_value:
        return FieldScore(**base, score="wrong", reason="no value extracted")
    if gold.must_contain and gold.scoring != "faithfulness":
        score, reason = token_score(out_text, gold.must_contain)
        return FieldScore(**base, score=score, reason=reason)
    if not judge:
        return FieldScore(**base, score="partial", reason="judge disabled; value present")
    score, reason = judge_field(field, reference or value_text(gold.value), out_text)
    return FieldScore(**base, score=score, reason=reason)


_NONE_PHRASES = {"none", "n/a", "na", "not stated", "not specified", "not required", "not found", "no"}


def _is_none_phrase(text: str) -> bool:
    """True for values that only say "nothing" ("None", "N/A", "Not stated")."""
    return text.strip().lower().rstrip(".") in _NONE_PHRASES


def token_score(text: str, tokens: list[str]) -> tuple[Score, str]:
    """exact if all must_contain tokens are present, partial if some, wrong if none."""
    missing = [token for token in tokens if not contains_phrase(text, token)]
    if not missing:
        return "exact", "all tokens present"
    if len(missing) < len(tokens):
        return "partial", f"missing tokens: {missing}"
    return "wrong", f"missing tokens: {missing}"


def judge_field(field: str, reference: str, out_text: str) -> tuple[Score, str]:
    """Ask REASON_MODEL to compare meaning; a failed judge call scores partial."""
    prompt = (JUDGE_PROMPT.replace("{field}", field).replace("{gold}", reference)
              .replace("{output}", out_text))
    try:
        verdict, _ = llm.structured(prompt, JudgeVerdict, model=settings.env.reason_model)
    except Exception as exc:  # noqa: BLE001 - the eval must finish even if the judge fails
        return "partial", f"judge failed: {type(exc).__name__}"
    return verdict.verdict, f"judge: {verdict.reason}"


def value_text(value: str | list[str] | None) -> str:
    """A value as one string (lists joined with '; ', None -> '')."""
    if value is None:
        return ""
    return "; ".join(value) if isinstance(value, list) else value


# ---------------------------------------------------------------- per bid


def score_bid(bid_id: str, gold: dict[str, GoldField], record: BidRecord, judge: bool) -> BidResult:
    """Score every field of one bid."""
    digest = _gold_digest(gold)
    scores = [
        score_field(field, gold_field, record.fields[field], judge,
                    reference=digest if gold_field.scoring == "faithfulness" else None)
        for field, gold_field in gold.items()
    ]
    counted = [s for s in scores if s.counted]
    exact = sum(s.score == "exact" for s in counted)
    partial = sum(s.score == "partial" for s in counted)
    wrong = sum(s.score == "wrong" for s in counted)
    return BidResult(
        bid_id=bid_id, fields=scores, exact=exact, partial=partial, wrong=wrong,
        scored_fields=len(counted),
        accuracy=round((exact + 0.5 * partial) / len(counted), 3) if counted else 0.0,
        fields_with_citations=sum(s.citations > 0 for s in scores),
        values_without_citation=sum(s.output != "" and s.citations == 0 for s in scores),
    )


def _gold_digest(gold: dict[str, GoldField]) -> str:
    """All gold values with a value, one per line (reference for faithfulness judging)."""
    lines = [f"{name}: {value_text(g.value)}" for name, g in gold.items() if g.value is not None]
    return "The summary must be consistent with these gold field values:\n" + "\n".join(lines)


def load_record(bid_id: str, outputs_dir: Path | None = None) -> BidRecord | None:
    """<outputs_dir>/<bid>.json as a BidRecord (default outputs/), or None if missing."""
    path = (outputs_dir or settings.path("outputs_dir")) / f"{bid_id}.json"
    if not path.exists():
        return None
    return BidRecord.model_validate_json(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- entry point


def run_extraction_eval(bid_ids: list[str] | None = None, judge: bool = True,
                        outputs_dir: Path | None = None, results_dir: Path = RESULTS_DIR) -> dict:
    """Score every bid with gold + output, write extraction.md/.json, return the results dict.

    `outputs_dir` / `results_dir` let a saved snapshot (e.g. the baseline
    records in eval/results/baseline/) be scored with the same rules.
    """
    results: list[BidResult] = []
    for bid_id in bid_ids or gold_bid_ids():
        record = load_record(bid_id, outputs_dir)
        if record is None:
            print(f"skipping {bid_id}: {bid_id}.json not found in {outputs_dir or settings.path('outputs_dir')}")
            continue
        results.append(score_bid(bid_id, load_gold(bid_id), record, judge))
    payload = {"judge": judge, "bids": [r.model_dump() for r in results]}
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "extraction.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (results_dir / "extraction.md").write_text(render_markdown(results, judge), encoding="utf-8")
    return payload


def render_markdown(results: list[BidResult], judge: bool) -> str:
    """Markdown report: a per-bid table and a summary table."""
    lines = ["# Extraction accuracy", "",
             f"Scoring: must_contain tokens (strict); LLM judge {'on' if judge else 'off'} for fields "
             "without tokens. Faithfulness-scored fields are shown but excluded from accuracy.", ""]
    for result in results:
        lines += [f"## {result.bid_id}", "", "| field | gold | output | score | citations |",
                  "|---|---|---|---|---|"]
        for s in result.fields:
            mark = "" if s.counted else " (not counted)"
            lines.append(f"| {s.field} | {_cell(s.gold)} | {_cell(s.output)} | {s.score}{mark} | {s.citations} |")
        lines.append("")
    lines += ["## Summary", "", "| bid | fields | exact | partial | wrong | accuracy | with citations | values w/o citation |",
              "|---|---|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r.bid_id} | {r.scored_fields} | {r.exact} | {r.partial} | {r.wrong} | "
                     f"{r.accuracy:.3f} | {r.fields_with_citations} | {r.values_without_citation} |")
    lines.append("")
    lines.append("accuracy = (exact + 0.5 * partial) / scored fields")
    return "\n".join(lines) + "\n"


def _cell(text: str, limit: int = 90) -> str:
    """Markdown-safe table cell, truncated."""
    text = text.replace("|", "\\|").replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def main() -> None:
    """CLI entry: python -m eval.run_extraction_eval."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bid", action="append", help="bid id (repeatable); default: all with gold")
    parser.add_argument("--no-judge", action="store_true", help="skip the LLM judge")
    args = parser.parse_args()
    payload = run_extraction_eval(args.bid, judge=not args.no_judge)
    for bid in payload["bids"]:
        print(f"{bid['bid_id']}: exact={bid['exact']} partial={bid['partial']} wrong={bid['wrong']} "
              f"accuracy={bid['accuracy']:.3f}")
    print(f"wrote {RESULTS_DIR / 'extraction.md'}")


if __name__ == "__main__":
    main()
