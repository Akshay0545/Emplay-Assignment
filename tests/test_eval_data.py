"""Tests for the evaluation data files and their loaders."""

import pytest
from pydantic import ValidationError

from eval.loaders import (
    GoldField,
    RetrievalQuestion,
    contains_phrase,
    gold_bid_ids,
    is_relevant,
    load_gold,
    load_retrieval_questions,
)
from rfp_intel.config import field_names, settings


def test_twenty_retrieval_questions_with_unique_ids():
    questions = load_retrieval_questions()
    assert len(questions) >= 20
    assert len({q.id for q in questions}) == len(questions)


def test_expected_files_exist():
    for q in load_retrieval_questions():
        path = settings.path("data_dir") / q.bid_id / q.expected_file
        assert path.exists(), f"Q{q.id}: {path} missing"


@pytest.mark.parametrize("bid_id", ["Bid1", "Bid2"])
def test_gold_files_cover_all_fields(bid_id):
    assert bid_id in gold_bid_ids()
    gold = load_gold(bid_id)
    assert list(gold) == field_names()


@pytest.mark.parametrize("bid_id", ["Bid1", "Bid2"])
def test_gold_not_found_entries_have_null_value(bid_id):
    for name, entry in load_gold(bid_id).items():
        if entry.status == "not_found":
            assert entry.value is None, name


def test_gold_field_rejects_value_on_not_found():
    with pytest.raises(ValidationError):
        GoldField(value="x", status="not_found")


def test_load_gold_rejects_wrong_field_set(tmp_path):
    (tmp_path / "BidX.json").write_text('{"Title": {"value": "t"}}', encoding="utf-8")
    with pytest.raises(ValueError, match="missing"):
        load_gold("BidX", gold_dir=tmp_path)


def test_phrase_matching_ignores_case_and_whitespace():
    assert contains_phrase("Memory: 16 GB\nRAM  DDR5", "16gb ram ddr5")
    assert not contains_phrase("8GB RAM", "16GB RAM")


def test_is_relevant_needs_file_and_phrase():
    q = RetrievalQuestion(id=1, question="q", bid_id="B", expected_file="a.pdf", key_phrase="Net 30")
    assert is_relevant("a.pdf", "payment is NET  30", q)
    assert not is_relevant("b.pdf", "payment is Net 30", q)
    assert not is_relevant("a.pdf", "payment is Net 45", q)
