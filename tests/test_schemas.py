"""Tests for rfp_intel.schemas and the config loaders they depend on."""

import pytest
from pydantic import ValidationError

from rfp_intel.config import field_names, load_fields, settings
from rfp_intel.schemas import (
    Answer,
    BidRecord,
    Chunk,
    Citation,
    DocType,
    FieldExtraction,
    FieldValue,
    Plan,
    SearchFilters,
    StepLog,
    Task,
)


def _not_found() -> FieldValue:
    return FieldValue(value=None, confidence=0.5, notes="Not found in documents", status="not_found")


def _record(fields: dict[str, FieldValue]) -> BidRecord:
    return BidRecord(
        bid_id="BidX",
        fields=fields,
        validation={"passed": 0, "failed": 0, "not_found": len(fields)},
        generated_at="2026-01-01T00:00:00Z",
        run_id="run-1",
    )


def test_fields_yaml_has_twenty_unique_fields():
    names = field_names()
    assert len(names) == 20
    assert len(set(names)) == 20
    assert {spec.group for spec in load_fields()} == {
        "commercial_legal", "dates_logistics", "product_specs", "report"
    }


def test_bid_record_accepts_exactly_the_configured_fields():
    record = _record({name: _not_found() for name in field_names()})
    assert set(record.fields) == set(field_names())


def test_bid_record_rejects_missing_field():
    fields = {name: _not_found() for name in field_names()[1:]}
    with pytest.raises(ValidationError, match="missing"):
        _record(fields)


def test_bid_record_rejects_unexpected_field():
    fields = {name: _not_found() for name in field_names()}
    fields["Made Up Field"] = _not_found()
    with pytest.raises(ValidationError, match="Made Up Field"):
        _record(fields)


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_field_value_confidence_bounds(confidence):
    with pytest.raises(ValidationError):
        FieldValue(value="x", confidence=confidence)


@pytest.mark.parametrize("value", ["JA-1", ["A", "B"], None])
def test_field_extraction_value_types(value):
    fx = FieldExtraction(field="Bid Number", value=value, evidence_ids=[], quotes=[], status="ok")
    assert fx.value == value


def test_field_extraction_rejects_unknown_status():
    with pytest.raises(ValidationError):
        FieldExtraction(field="f", value=None, evidence_ids=[], quotes=[], status="maybe")


def test_chunk_json_round_trip_keeps_doc_type_enum():
    chunk = Chunk(
        chunk_id="abc:2:0", bid_id="BidX", file_name="a.pdf", doc_type=DocType.ADDENDUM,
        addendum_number=1, page=2, doc_date=None, section="Q31", kind="qa",
        header="BidX | addendum 1 | a.pdf | p2 | Q31", text="Q. A.", token_count=3,
    )
    again = Chunk.model_validate_json(chunk.model_dump_json())
    assert again == chunk
    assert again.doc_type is DocType.ADDENDUM


def test_defaults_are_not_shared_between_instances():
    a, b = Task(group="qa"), Task(group="qa")
    a.fields.append("x")
    assert b.fields == []
    assert isinstance(a.filters, SearchFilters)


def test_plan_and_answer_parse_from_json():
    plan = Plan.model_validate_json('{"mode": "qa", "question_type": "comparison", "tasks": []}')
    assert plan.question_type == "comparison"
    answer = Answer(
        question="q", answer="Not found in documents", citations=[
            Citation(file="a.pdf", page=1, chunk_id="c", quote="q")
        ], confidence=0.5, bids=["BidX"],
    )
    assert answer.citations[0].page == 1


def test_step_log_defaults():
    log = StepLog(run_id="r", step=1, agent="a", node="n", started_at="t",
                  input_summary="", tool_calls=[], output_summary="", latency_ms=5)
    assert (log.tokens_in, log.tokens_out, log.cost_usd, log.error) == (0, 0, 0.0, None)


def test_settings_named_retrieval_configs():
    assert settings.retrieval_for("vector").use_bm25 is False
    assert settings.retrieval_for("hybrid").use_reranker is False
    assert settings.retrieval_for("hybrid_rerank").use_reranker is True
    assert settings.retrieval_for(None) == settings.retrieval
    with pytest.raises(ValueError):
        settings.retrieval_for("nope")


def test_settings_precedence_are_doc_types():
    assert settings.precedence[0] is DocType.ADDENDUM
    assert set(settings.precedence) == set(DocType)
