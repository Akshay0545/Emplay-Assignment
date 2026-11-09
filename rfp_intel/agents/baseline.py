"""Single-pass baseline extractor used as the accuracy reference (Phase 5).

    record = run_baseline("Bid1")          # searches, extracts, writes outputs/Bid1.json

Per field: queries from the field description and search hints -> hybrid search
with a bid filter -> evidence numbered [E1], [E2], ... per field group -> one
structured LLM call per group -> evidence ids mapped to Citations -> BidRecord.
No validator or addendum reconciliation yet; that is the Phase 6 graph.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Iterable

from rfp_intel import llm
from rfp_intel.config import FieldSpec, load_fields, settings
from rfp_intel.logging_utils import new_run_id, now_iso
from rfp_intel.schemas import (
    BidRecord,
    Citation,
    FieldExtraction,
    FieldExtractions,
    FieldValue,
    SearchFilters,
    SearchHit,
)
from rfp_intel.search.engine import SearchEngine

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent / "prompts" / "extract.md"
SUMMARY_FIELD = "Bid Summary"
REPORT_GROUP = "report"
RETRIEVAL_CONFIG = "hybrid_rerank"
QUOTE_LIMIT = 300
NOT_FOUND_NOTE = "Not found in documents"
NOT_FOUND_CONFIDENCE = 0.5
NO_CITATION_CONFIDENCE = 0.2
SUMMARY_CONFIDENCE = 0.6

SUMMARY_PROMPT = (
    "You are a procurement analyst. Write a summary of bid \"{bid_id}\" in 3 to 6 sentences "
    "using ONLY the extracted field values below. Do not add facts that are not listed, "
    "do not speculate, and do not mention fields that are missing.\n\nExtracted fields\n{fields}"
)

GroupHits = dict[str, list[SearchHit]]


def run_baseline(bid_id: str, engine: SearchEngine | None = None, model: str | None = None) -> BidRecord:
    """Extract all configured fields for an indexed bid and write outputs/<bid>.json."""
    owns_engine = engine is None
    engine = engine or SearchEngine()
    model = model or settings.env.extract_model
    try:
        values = _extract_all(bid_id, engine, model)
    finally:
        if owns_engine:
            engine.close()
    values[SUMMARY_FIELD] = _summarize(bid_id, values)
    record = build_record(bid_id, values)
    save_record(record)
    return record


def build_record(bid_id: str, values: dict[str, FieldValue]) -> BidRecord:
    """Assemble the deliverable with validation counts (passed = status ok)."""
    counts = {"passed": 0, "failed": 0, "not_found": 0}
    for value in values.values():
        counts["passed" if value.status == "ok" else value.status] += 1
    ordered = {spec.name: values[spec.name] for spec in load_fields()}
    return BidRecord(bid_id=bid_id, fields=ordered, validation=counts,
                     generated_at=now_iso(), run_id=new_run_id())


def save_record(record: BidRecord) -> Path:
    """Write the record to outputs/<bid>.json and return the path."""
    out_dir = settings.path("outputs_dir")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{record.bid_id}.json"
    path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------- retrieval


def _extract_all(bid_id: str, engine: SearchEngine, model: str) -> dict[str, FieldValue]:
    """Retrieve and extract every non-report field, one LLM call per group."""
    values: dict[str, FieldValue] = {}
    for group, specs in _groups().items():
        hits = {spec.name: _search_field(engine, bid_id, spec) for spec in specs}
        values.update(extract_group(bid_id, group, specs, hits, model))
    return values


def _groups() -> dict[str, list[FieldSpec]]:
    """Extractable fields keyed by group, in config order."""
    groups: dict[str, list[FieldSpec]] = {}
    for spec in load_fields():
        if spec.group != REPORT_GROUP:
            groups.setdefault(spec.group, []).append(spec)
    return groups


def build_queries(spec: FieldSpec) -> list[str]:
    """Two or three short queries: the description's first sentence plus hint groups."""
    first_sentence = re.split(r"(?<=[.!?])\s", spec.description.strip(), maxsplit=1)[0]
    queries = [first_sentence]
    hints = spec.search_hints
    half = (len(hints) + 1) // 2
    for part in (hints[:half], hints[half:]):
        if part:
            queries.append(", ".join(part))
    return queries


def _search_field(engine: SearchEngine, bid_id: str, spec: FieldSpec) -> list[SearchHit]:
    """Unique hits for one field across its queries, best score first, capped at top_k."""
    filters = SearchFilters(bid_id=bid_id, doc_types=spec.doc_types)
    top_k = spec.top_k or settings.agents.evidence_top_k
    found: dict[str, SearchHit] = {}
    for query in build_queries(spec):
        for hit in _safe_search(engine, query, filters, top_k):
            key = hit.chunk.chunk_id
            if key not in found or hit.score > found[key].score:
                found[key] = hit
    return sorted(found.values(), key=lambda h: h.score, reverse=True)[:top_k]


def _safe_search(engine: SearchEngine, query: str, filters: SearchFilters, top_k: int) -> list[SearchHit]:
    """Run one search; a failing search counts as empty retrieval, never a crash."""
    try:
        return engine.search(query, filters, top_k=top_k, config=RETRIEVAL_CONFIG)
    except Exception as exc:  # noqa: BLE001 - retrieval errors must not stop the run
        logger.warning("search failed for %r: %s", query, exc)
        return []


# ---------------------------------------------------------------- extraction


def extract_group(bid_id: str, group: str, specs: list[FieldSpec], hits: GroupHits,
                  model: str) -> dict[str, FieldValue]:
    """Extract one field group: not_found without evidence, else one LLM call."""
    values = {spec.name: not_found_value() for spec in specs if not hits.get(spec.name)}
    with_hits = [spec for spec in specs if hits.get(spec.name)]
    if not with_hits:
        return values
    evidence = number_evidence(hits)
    prompt = build_prompt(bid_id, group, with_hits, evidence)
    try:
        result, usage = llm.structured(prompt, FieldExtractions, model=model)
    except Exception as exc:  # noqa: BLE001 - one bad group must not crash the run
        logger.warning("extraction failed for group %s: %s", group, exc)
        note = f"LLM call failed: {type(exc).__name__}: {str(exc)[:200]}"
        values.update({spec.name: failed_value(note) for spec in with_hits})
        return values
    logger.info("group %s: %d fields, tokens in=%d out=%d", group, len(with_hits),
                usage.tokens_in, usage.tokens_out)
    scores = score_range(evidence)
    by_name = {_key(item.field): item for item in result.items}
    for spec in with_hits:
        values[spec.name] = to_field_value(by_name.get(_key(spec.name)), evidence, scores)
    return values


def number_evidence(hits: GroupHits) -> dict[str, SearchHit]:
    """Unique hits across the group's fields, numbered E1, E2, ... in first-seen order."""
    evidence: dict[str, SearchHit] = {}
    seen: set[str] = set()
    for field_hits in hits.values():
        for hit in field_hits:
            if hit.chunk.chunk_id not in seen:
                seen.add(hit.chunk.chunk_id)
                evidence[f"E{len(evidence) + 1}"] = hit
    return evidence


def build_prompt(bid_id: str, group: str, specs: list[FieldSpec], evidence: dict[str, SearchHit]) -> str:
    """Fill the extraction prompt template."""
    fields_block = "\n".join(f"- {spec.name}: {spec.description}" for spec in specs)
    evidence_block = "\n\n".join(
        f"[{eid}] ({hit.chunk.file_name}, p.{hit.chunk.page}) {hit.chunk.header}\n{hit.chunk.text}"
        for eid, hit in evidence.items()
    )
    template = PROMPT_PATH.read_text(encoding="utf-8")
    for key, text in (("bid_id", bid_id), ("group", group), ("fields_block", fields_block),
                      ("evidence_block", evidence_block)):
        template = template.replace("{" + key + "}", text)
    return template


def to_field_value(item: FieldExtraction | None, evidence: dict[str, SearchHit],
                   scores: dict[str, float]) -> FieldValue:
    """Turn the LLM's extraction into a FieldValue with citations and confidence."""
    if item is None or item.status == "not_found" or item.value in (None, "", []):
        return not_found_value()
    citations = map_citations(item, evidence)
    if not citations:
        note = "no citation" + (f"; {item.notes}" if item.notes else "")
        return FieldValue(value=item.value, sources=[], confidence=NO_CITATION_CONFIDENCE,
                          notes=note, status="failed")
    best = max(scores.get(c.chunk_id, 0.5) for c in citations)
    return FieldValue(value=item.value, sources=citations, confidence=confidence(best, len(citations)),
                      notes=item.notes, status="ok")


def map_citations(item: FieldExtraction, evidence: dict[str, SearchHit]) -> list[Citation]:
    """Evidence ids -> Citations; unknown ids are dropped, quotes verified against the chunk."""
    citations: list[Citation] = []
    for index, raw_id in enumerate(item.evidence_ids):
        hit = evidence.get(_evidence_key(raw_id))
        if hit is None:
            continue
        preferred = item.quotes[index] if index < len(item.quotes) else None
        quote = pick_quote(hit.chunk.text, preferred, item.quotes)
        citations.append(Citation(file=hit.chunk.file_name, page=hit.chunk.page,
                                  chunk_id=hit.chunk.chunk_id, quote=quote))
    return citations


def pick_quote(chunk_text: str, preferred: str | None, candidates: list[str]) -> str:
    """The first model quote found in the chunk (whitespace-insensitively), else the chunk start."""
    ordered = ([preferred] if preferred else []) + [q for q in candidates if q != preferred]
    for quote in ordered:
        span = find_span(chunk_text, quote)
        if span:
            return span[:QUOTE_LIMIT]
    return chunk_text[:QUOTE_LIMIT]


def find_span(text: str, quote: str) -> str | None:
    """The exact span of `text` matching `quote` ignoring whitespace differences, or None."""
    compact_quote = re.sub(r"\s+", "", quote)
    if not compact_quote:
        return None
    positions = [i for i, ch in enumerate(text) if not ch.isspace()]
    compact_text = "".join(text[i] for i in positions)
    start = compact_text.lower().find(compact_quote.lower())
    if start < 0:
        return None
    end = start + len(compact_quote) - 1
    return text[positions[start]: positions[end] + 1]


def score_range(evidence: dict[str, SearchHit]) -> dict[str, float]:
    """Min-max normalised score per chunk id within the group's evidence (0.5 if flat)."""
    if not evidence:
        return {}
    values = [hit.score for hit in evidence.values()]
    low, high = min(values), max(values)
    if high == low:
        return {hit.chunk.chunk_id: 0.5 for hit in evidence.values()}
    return {hit.chunk.chunk_id: (hit.score - low) / (high - low) for hit in evidence.values()}


def confidence(best_score: float, n_citations: int) -> float:
    """0.4*best normalised score + 0.3 (validator, counted as passed) + 0.2 if >=2 sources + 0.1."""
    value = 0.4 * best_score + 0.3 + (0.2 if n_citations >= 2 else 0.0) + 0.1
    return round(min(max(value, 0.0), 1.0), 3)


def not_found_value() -> FieldValue:
    """The value every missing field gets."""
    return FieldValue(value=None, status="not_found", notes=NOT_FOUND_NOTE,
                      confidence=NOT_FOUND_CONFIDENCE)


def failed_value(note: str) -> FieldValue:
    """The value a field gets when its extraction call failed."""
    return FieldValue(value=None, status="failed", notes=note, confidence=0.0)


# ---------------------------------------------------------------- summary


def _summarize(bid_id: str, values: dict[str, FieldValue]) -> FieldValue:
    """Bid Summary from the ok fields only (REASON_MODEL); not_found when nothing is ok."""
    ok_fields = {name: value for name, value in values.items() if value.status == "ok"}
    if not ok_fields:
        return not_found_value()
    lines = "\n".join(f"- {name}: {value_text(value.value)}" for name, value in ok_fields.items())
    prompt = SUMMARY_PROMPT.replace("{bid_id}", bid_id).replace("{fields}", lines)
    try:
        summary, _ = llm.text(prompt, model=settings.env.reason_model)
    except Exception as exc:  # noqa: BLE001 - the summary must not crash the run
        logger.warning("summary failed: %s", exc)
        return failed_value(f"LLM call failed: {type(exc).__name__}: {str(exc)[:200]}")
    return FieldValue(value=summary.strip(), sources=_union_citations(ok_fields.values()),
                      confidence=SUMMARY_CONFIDENCE, notes=f"Summarises {len(ok_fields)} ok fields")


def _union_citations(values: Iterable[FieldValue]) -> list[Citation]:
    """All citations of the given fields, without duplicates."""
    seen: set[tuple[str, str]] = set()
    union: list[Citation] = []
    for value in values:
        for citation in value.sources:
            key = (citation.chunk_id, citation.quote)
            if key not in seen:
                seen.add(key)
                union.append(citation)
    return union


def value_text(value: str | list[str] | None) -> str:
    """A field value as one string (lists joined with '; ')."""
    if value is None:
        return ""
    return "; ".join(value) if isinstance(value, list) else value


# ---------------------------------------------------------------- helpers


def _key(name: str) -> str:
    """Field name key tolerant to case and whitespace differences."""
    return re.sub(r"\s+", "", name).lower()


def _evidence_key(raw_id: str) -> str:
    """Normalise an evidence id the model wrote ("E3", "e3", "[E3]", "3") to "E3"."""
    digits = re.sub(r"\D", "", raw_id)
    return f"E{digits}" if digits else raw_id.strip()
