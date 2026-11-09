"""Baseline extractor and extraction-eval scoring tests (fake engine, fake LLM, no network)."""

from __future__ import annotations

import pytest

from eval.loaders import GoldField
from eval.run_extraction_eval import score_field
from rfp_intel import llm
from rfp_intel.agents import baseline
from rfp_intel.config import field_names, load_fields
from rfp_intel.llm import LLMError
from rfp_intel.schemas import (
    BidRecord,
    Chunk,
    DocType,
    FieldExtraction,
    FieldExtractions,
    FieldValue,
    SearchFilters,
    SearchHit,
    Usage,
)

BID = "TestBid"
LONG_TEXT = ("Solicitation Number JA-1 Title: Devices for Everyone. " * 12).strip()  # > 300 chars


def make_hit(chunk_id: str, text: str, score: float = 1.0, page: int = 2) -> SearchHit:
    """A SearchHit with the given chunk text."""
    chunk = Chunk(chunk_id=chunk_id, bid_id=BID, file_name="rfp.pdf", doc_type=DocType.RFP,
                  addendum_number=None, page=page, doc_date=None, section="General", kind="text",
                  header=f"{BID} | rfp | rfp.pdf | p{page} | General", text=text, token_count=50)
    return SearchHit(chunk=chunk, score=score, ranks={"rerank": 1})


class FakeEngine:
    """Returns the hits registered for any query hint found in the query text."""

    def __init__(self, hits_by_hint: dict[str, list[SearchHit]]) -> None:
        self.hits_by_hint = hits_by_hint
        self.queries: list[tuple[str, SearchFilters]] = []

    def search(self, query: str, filters: SearchFilters | None = None, top_k: int | None = None,
               config: str | None = None) -> list[SearchHit]:
        self.queries.append((query, filters))
        return [hit for hint, hits in self.hits_by_hint.items() if hint in query.lower() for hit in hits]

    def close(self) -> None:
        pass


class FakeLLM:
    """Stands in for llm.structured / llm.text; answers with the items whose field is in the prompt."""

    def __init__(self, items: list[FieldExtraction], fail_if: str | None = None) -> None:
        self.items = items
        self.fail_if = fail_if
        self.prompts: list[str] = []

    def structured(self, prompt, model_cls, model, system=None):
        self.prompts.append(prompt)
        if self.fail_if and self.fail_if in prompt:
            raise LLMError("boom")
        assert model_cls is FieldExtractions
        return FieldExtractions(items=[i for i in self.items if f"- {i.field}:" in prompt]), Usage()

    def text(self, prompt, model, system=None):
        self.prompts.append(prompt)
        return "A short summary. It has two sentences.", Usage()


@pytest.fixture
def outputs_dir(tmp_path, monkeypatch):
    """Redirect outputs/ to a temp folder."""
    monkeypatch.setattr(baseline.settings.paths, "outputs_dir", str(tmp_path / "outputs"))
    return tmp_path / "outputs"


@pytest.fixture
def engine():
    """Hits for Bid Number (two chunks), Title (shares one chunk) and Due Date (one chunk)."""
    hits = {
        "solicitation number": [make_hit("c1", "Solicitation Number JA-1 for laptops.", 2.0),
                                make_hit("c2", LONG_TEXT, 1.0)],
        "solicitation title": [make_hit("c1", "Solicitation Number JA-1 for laptops.", 2.0)],
        "due date": [make_hit("c3", "Proposals are due June 1, 2030 at 2:00 PM CST.", 3.0, page=5)],
    }
    return FakeEngine(hits)


def install(monkeypatch, fake: FakeLLM) -> None:
    monkeypatch.setattr(llm, "structured", fake.structured)
    monkeypatch.setattr(llm, "text", fake.text)


def test_record_has_all_fields_and_validates(engine, outputs_dir, monkeypatch):
    fake = FakeLLM([
        FieldExtraction(field="Bid Number", value="JA-1", evidence_ids=["E1"],
                        quotes=["Solicitation   Number JA-1"], status="ok"),
        FieldExtraction(field="Due Date", value="June 1, 2030 2:00 PM CST", evidence_ids=["E1"],
                        quotes=["due June 1, 2030 at 2:00 PM CST"], status="ok"),
    ])
    install(monkeypatch, fake)
    record = baseline.run_baseline(BID, engine=engine)

    assert set(record.fields) == set(field_names()) and len(record.fields) == 20
    saved = BidRecord.model_validate_json((outputs_dir / f"{BID}.json").read_text(encoding="utf-8"))
    assert saved.bid_id == BID and saved.run_id == record.run_id
    bid_number = record.fields["Bid Number"]
    assert bid_number.status == "ok" and bid_number.value == "JA-1"
    assert bid_number.sources[0].quote == "Solicitation Number JA-1"      # exact chunk span
    assert bid_number.sources[0].page == 2 and bid_number.sources[0].file == "rfp.pdf"
    assert 0.4 <= bid_number.confidence <= 1.0
    summary = record.fields["Bid Summary"]
    assert summary.status == "ok" and summary.confidence == 0.6
    assert {c.chunk_id for c in summary.sources} == {"c1", "c3"}
    counts = record.validation
    assert counts["passed"] == 3 and counts["passed"] + counts["failed"] + counts["not_found"] == 20
    for value in record.fields.values():                                   # guardrail
        assert value.value is None or value.status == "failed" or value.sources
    # every search was scoped to the bid
    assert all(f.bid_id == BID for _, f in engine.queries)


def test_field_without_hits_is_not_found_without_llm(engine, outputs_dir, monkeypatch):
    fake = FakeLLM([])
    install(monkeypatch, fake)
    record = baseline.run_baseline(BID, engine=engine)

    bond = record.fields["Bid Bond Requirement"]
    assert bond.status == "not_found" and bond.value is None
    assert bond.notes == "Not found in documents" and bond.confidence == 0.5
    assert not any("- Bid Bond Requirement:" in p for p in fake.prompts)
    # product_specs has no hits at all -> no LLM call for that group, and no summary (no ok fields)
    assert not any("product_specs" in p for p in fake.prompts)
    assert record.fields["Bid Summary"].status == "not_found"


def test_unknown_evidence_ids_dropped_and_missing_citation_fails(engine, outputs_dir, monkeypatch):
    fake = FakeLLM([
        FieldExtraction(field="Bid Number", value="JA-1", evidence_ids=["E99", "E1"],
                        quotes=["nope", "JA-1 for laptops"], status="ok"),
        FieldExtraction(field="Title", value="Devices", evidence_ids=["E42"], quotes=["x"], status="ok"),
    ])
    install(monkeypatch, fake)
    record = baseline.run_baseline(BID, engine=engine)

    number = record.fields["Bid Number"]
    assert [c.chunk_id for c in number.sources] == ["c1"] and number.status == "ok"
    title = record.fields["Title"]
    assert title.status == "failed" and title.value == "Devices" and title.sources == []
    assert title.confidence == 0.2 and "no citation" in title.notes


def test_quote_fallback_to_chunk_start(engine, outputs_dir, monkeypatch):
    fake = FakeLLM([
        FieldExtraction(field="Bid Number", value="JA-1", evidence_ids=["E2"],
                        quotes=["this quote is not in the chunk"], status="ok"),
    ])
    install(monkeypatch, fake)
    record = baseline.run_baseline(BID, engine=engine)

    citation = record.fields["Bid Number"].sources[0]
    assert citation.chunk_id == "c2"
    assert citation.quote == LONG_TEXT[:300] and len(citation.quote) == 300


def test_llm_error_fails_only_that_group(engine, outputs_dir, monkeypatch):
    fake = FakeLLM([
        FieldExtraction(field="Due Date", value="June 1, 2030", evidence_ids=["E1"],
                        quotes=["June 1, 2030"], status="ok"),
    ], fail_if="field group: commercial_legal")
    install(monkeypatch, fake)
    record = baseline.run_baseline(BID, engine=engine)

    commercial = [s.name for s in load_fields() if s.group == "commercial_legal"]
    for name in ("Bid Number", "Title"):                                   # had hits -> failed
        assert record.fields[name].status == "failed" and record.fields[name].value is None
        assert "LLMError" in record.fields[name].notes
    assert all(record.fields[n].status in {"failed", "not_found"} for n in commercial)
    assert record.fields["Due Date"].status == "ok"                        # other group unaffected
    assert record.fields["Bid Summary"].status == "ok"


def test_find_span_is_whitespace_insensitive():
    assert baseline.find_span("Due  date:\n July 9,\t2024", "due date: july 9, 2024") == "Due  date:\n July 9,\t2024"
    assert baseline.find_span("some text", "missing") is None


# ---------------------------------------------------------------- eval scoring


def fv(value, status="ok", n_citations=1) -> FieldValue:
    from rfp_intel.schemas import Citation

    sources = [Citation(file="f", page=1, chunk_id="c", quote="q")] * n_citations
    return FieldValue(value=value, status=status, confidence=0.9, sources=sources if value else [])


def test_scoring_tokens():
    gold = GoldField(value="July 9, 2024 2:00 PM", must_contain=["July 9, 2024", "2:00"])
    assert score_field("Due Date", gold, fv("Due July 9,2024 at 2:00 PM"), judge=False).score == "exact"
    assert score_field("Due Date", gold, fv("Due July 9, 2024"), judge=False).score == "partial"
    assert score_field("Due Date", gold, fv("June 27"), judge=False).score == "wrong"
    assert score_field("Due Date", gold, fv(["x", "2:00 pm", "july 9, 2024"]), judge=False).score == "exact"


def test_scoring_not_found_and_missing():
    nf = GoldField(value=None, status="not_found")
    assert score_field("Bond", nf, fv(None, status="not_found")).score == "exact"
    assert score_field("Bond", nf, fv(None, status="failed")).score == "exact"
    assert score_field("Bond", nf, fv("5%")).score == "wrong"
    gold = GoldField(value="EPCNT", must_contain=["EPCNT"])
    assert score_field("Coop", gold, fv(None, status="not_found")).score == "wrong"


def test_scoring_without_tokens_uses_judge_or_partial():
    gold = GoldField(value="June 10, 2024 2:00 PM via Teams", must_contain=[])
    result = score_field("Pre Bid Meeting", gold, fv("June 10, 2024, Teams", n_citations=2), judge=False)
    assert result.score == "partial" and result.citations == 2 and result.counted


def test_gold_none_matches_not_found_or_none_value():
    gold = GoldField(value="None", must_contain=[])
    assert score_field("Pre Bid Meeting", gold, fv(None, status="not_found"), judge=False).score == "exact"
    assert score_field("Pre Bid Meeting", gold, fv("N/A"), judge=False).score == "exact"
    assert score_field("Pre Bid Meeting", gold, fv("June 10, 2024 via Teams"), judge=False).score == "wrong"
    faithful = GoldField(value=None, must_contain=[], scoring="faithfulness")
    result = score_field("Bid Summary", faithful, fv("A summary."), judge=False)
    assert result.score == "partial" and not result.counted
