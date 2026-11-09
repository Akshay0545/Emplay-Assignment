"""Tests for the deterministic validator rules."""

from datetime import datetime, timezone

import pytest

from rfp_intel.agents import rules
from rfp_intel.schemas import Chunk, Citation, DocType, FieldValue


def _chunk(cid: str, text: str) -> Chunk:
    return Chunk(chunk_id=cid, bid_id="B", file_name="f.pdf", doc_type=DocType.RFP, addendum_number=None,
                 page=1, doc_date=None, section=None, kind="text", header="", text=text, token_count=1)


def _cite(cid: str, quote: str) -> Citation:
    return Citation(file="f.pdf", page=1, chunk_id=cid, quote=quote)


def _fv(value, *sources) -> FieldValue:
    return FieldValue(value=value, sources=list(sources), confidence=0.5)


def test_has_citation():
    assert rules.has_citation(_fv(None))
    assert not rules.has_citation(_fv("x"))
    assert rules.has_citation(_fv("x", _cite("c", "x")))


def test_quote_in_chunk_whitespace_insensitive():
    chunks = {"c": _chunk("c", "The new due date\nwill be July 9,  2024 at 2:00 PM CST.")}
    assert rules.quote_in_chunk(_cite("c", "July 9, 2024 at 2:00 PM"), chunks)
    assert not rules.quote_in_chunk(_cite("c", "July 10, 2024"), chunks)
    assert not rules.quote_in_chunk(_cite("missing", "July 9, 2024"), chunks)
    assert not rules.quote_in_chunk(_cite("c", "   "), chunks)
    assert rules.all_quotes_in_chunks(_fv("July 9", _cite("c", "due date")), chunks)


@pytest.mark.parametrize(("field", "value", "ok"), [
    ("Due Date", "July 9, 2024 at 2:00 PM Central", True),
    ("Due Date", "2024-07-09", True),
    ("Due Date", "sometime next summer", False),
    ("Pre Bid Meeting", "None", True),
    ("contact_info", "Jasmine Alzate, JALZATE@dallasisd.org", True),
    ("contact_info", ["Tamaira Hawkins 410-260-7533"], True),
    ("contact_info", "Procurement Services", False),
    ("Title", "anything goes", True),
])
def test_format_ok(field, value, ok):
    assert rules.format_ok(field, _fv(value)) is ok


@pytest.mark.parametrize(("text", "expected"), [
    ("July 9, 2024 at 2:00 PM CST", datetime(2024, 7, 9, 20, 0, tzinfo=timezone.utc)),
    ("07/09/2024 03:00 PM EDT", datetime(2024, 7, 9, 19, 0, tzinfo=timezone.utc)),
    ("Due 2:00 PM Central on June 27, 2024", datetime(2024, 6, 27, 20, 0, tzinfo=timezone.utc)),
    ("27-JUN-2024", datetime(2024, 6, 27, 0, 0, tzinfo=timezone.utc)),
    ("06/10/2024 14:00 EDT", datetime(2024, 6, 10, 18, 0, tzinfo=timezone.utc)),
    ("no date here", None),
])
def test_parse_datetime_normalizes_time_zones(text, expected):
    assert rules.parse_datetime(text) == expected


def test_edt_and_central_same_instant():
    assert rules.parse_datetime("July 9, 2024 2:00 PM CDT") == rules.parse_datetime("07/09/2024 03:00 PM EDT")


def test_consistency_due_after_publication_and_prebid_before_due():
    drafts = {
        "Publication Date": _fv("05/29/2024 11:16 AM EDT"),
        "Due Date": _fv("July 9, 2024 2:00 PM CST"),
        "Pre Bid Meeting": _fv("June 10, 2024 2:00 PM CST, Teams"),
    }
    assert rules.consistent("Due Date", drafts)
    assert rules.consistent("Pre Bid Meeting", drafts)
    drafts["Pre Bid Meeting"] = _fv("August 1, 2024 2:00 PM CST")
    assert not rules.consistent("Pre Bid Meeting", drafts)
    drafts["Due Date"] = _fv("May 1, 2024")
    assert not rules.consistent("Due Date", drafts)


def test_consistency_model_and_part_numbers():
    drafts = {"Product": _fv("30 Dell Latitude 5550 laptops, 30 WD22TB4 docks"),
              "Model_no": _fv(["Dell Latitude 5550", "WD22TB4"]), "Part_no": _fv("210-BLYZ")}
    assert rules.consistent("Model_no", drafts)
    assert not rules.consistent("Part_no", drafts)
    assert rules.consistent("Part_no", drafts, evidence_texts=["Base SKU 210-BLYZ"])
    assert rules.consistent("Part_no", {"Part_no": _fv(None)})
    assert rules.consistent("Title", drafts)
