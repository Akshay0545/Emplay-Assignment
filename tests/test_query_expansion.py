"""Tests for query expansion, filter parsing and RRF."""

from rfp_intel.schemas import DocType, SearchFilters
from rfp_intel.search.engine import merge_filters
from rfp_intel.search.fusion import rrf
from rfp_intel.search.query import MAX_QUERY_VARIANTS, expand_query, parse_filters

BIDS = ["Bid1", "Bid2"]


def test_expansion_adds_synonym_variants_original_first():
    variants = expand_query("What is the due date?")
    assert variants[0] == "What is the due date?"
    assert "What is the closing date?" in variants
    assert len(variants) <= MAX_QUERY_VARIANTS


def test_expansion_matches_whole_phrases_only():
    assert expand_query("bondage of contracts") == ["bondage of contracts"]
    assert len(expand_query("bid bond amount")) > 1


def test_expansion_without_synonyms_is_identity():
    assert expand_query("processor model") == ["processor model"]


def test_scoped_specs_query_from_plan():
    filters, cleaned = parse_filters("only search Bid2 specs processor", BIDS)
    assert filters.bid_id == "Bid2" and filters.doc_types == [DocType.SPECS]
    assert cleaned == "processor"


def test_topic_word_is_not_a_filter():
    filters, cleaned = parse_filters("Which affidavit concerns mercury?", BIDS)
    assert filters == SearchFilters()
    assert cleaned == "Which affidavit concerns mercury?"


def test_addendum_number_filter():
    filters, cleaned = parse_filters("What changed in Addendum 2 for Bid1?", BIDS)
    assert filters.addendum_number == 2 and filters.doc_types == [DocType.ADDENDUM]
    assert filters.bid_id == "Bid1" and "Addendum 2" in cleaned


def test_in_the_rfp_and_bid_page_scopes():
    assert parse_filters("delivery terms in the RFP", BIDS)[0].doc_types == [DocType.RFP]
    assert parse_filters("closing time from the portal", BIDS)[0].doc_types == [DocType.BID_PAGE]
    assert parse_filters("warranty, specs only", BIDS)[0].doc_types == [DocType.SPECS]


def test_bid_ids_case_insensitive_and_multiple():
    filters, cleaned = parse_filters("compare bid1 and BID2 warranty", BIDS)
    assert filters.bid_ids == ["Bid1", "Bid2"] and filters.bid_id is None
    assert cleaned == "compare and warranty"
    assert parse_filters("Bid10 terms", BIDS)[0] == SearchFilters()


def test_explicit_filters_win():
    parsed = SearchFilters(bid_ids=["Bid1", "Bid2"], doc_types=[DocType.SPECS])
    merged = merge_filters(parsed, SearchFilters(bid_id="Bid1"))
    assert merged.bid_id == "Bid1" and merged.bid_ids is None
    assert merged.doc_types == [DocType.SPECS]
    assert merge_filters(parsed, None) == parsed


def test_rrf_scores_and_order():
    fused = rrf([["a", "b", "c"], ["b", "a"]], k=60)
    assert [i for i, _ in fused][:2] in (["a", "b"], ["b", "a"])
    scores = dict(fused)
    assert scores["a"] == 1 / 61 + 1 / 62 and scores["c"] == 1 / 63
    assert rrf([], k=60) == []
