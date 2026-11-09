"""Tests for rfp_intel.search.chunker."""

import pytest

from rfp_intel.config import settings
from rfp_intel.ingestion.loader import load_bid_folder
from rfp_intel.schemas import DocType, Document, Page
from rfp_intel.search.chunker import (
    chunk_document,
    count_tokens,
    detect_sections,
    embed_text,
    is_heading,
)


def _doc(*pages: str, doc_type=DocType.RFP, tables=None, addendum=None, name="doc.pdf") -> Document:
    return Document(
        doc_id="d1", bid_id="BidX", file_name=name, doc_type=doc_type, addendum_number=addendum,
        pages=[Page(number=i + 1, text=t, tables=(tables or {}).get(i + 1, []))
               for i, t in enumerate(pages)],
    )


@pytest.fixture
def small_chunks(monkeypatch):
    monkeypatch.setattr(settings.chunking, "max_tokens", 60)
    monkeypatch.setattr(settings.chunking, "overlap_tokens", 10)
    monkeypatch.setattr(settings.chunking, "min_tokens", 5)


@pytest.mark.parametrize(("line", "expected"), [
    ("LENGTH OF CONTRACT", True),
    ("2.1 Line Information", True),
    ("Tier 3 Student Windows Laptop Minimum Requirements", True),
    ("9400 North Central Expressway, Suite 1510", False),
    ("The term of this proposal shall be three years.", False),
    ("• Memory: Minimum 16GB RAM DDR5 Memory", False),
    ("12. Are stated quantities one-time buys?", False),
    ("06/01/2024 at 2:00 PM EDT", False),
    ("PORFP NUMBER: E20P4600040", True),
])
def test_is_heading(line, expected):
    assert is_heading(line) is expected


def test_detect_sections_merges_multiline_headings():
    text = "Intro text.\nSCOPE AND SPECIFICATIONS OF THE\nPROPOSAL\nBody one.\n1.2 Terms\nBody two."
    assert detect_sections(text) == [
        (None, "Intro text."),
        ("SCOPE AND SPECIFICATIONS OF THE PROPOSAL", "Body one."),
        ("1.2 Terms", "Body two."),
    ]


def test_qa_pairs_stay_whole_across_pages():
    doc = _doc(
        "ADDENDUM No. 1\n\n1. Is etching required on monitors?\nAnswer: Only laptops.\n\n"
        "2. When are quotes requested?\nAnswer: Starting in",
        "September for each project.\n\n3. Is Form X required?\nAnswer: Yes.",
        doc_type=DocType.ADDENDUM, addendum=1,
    )
    qa = [c for c in chunk_document(doc) if c.kind == "qa"]
    assert [c.section for c in qa] == ["Q1", "Q2", "Q3"]
    assert qa[1].page == 1
    assert "When are quotes requested?" in qa[1].text and "September for each project." in qa[1].text
    assert all("Answer:" in c.text for c in qa)


def test_numbered_list_inside_answer_does_not_split_the_pair():
    doc = _doc("1. What is required?\nAnswer: Both items:\n1. an asset tag\n2. etching\n\n"
               "2. Next question?\nAnswer: No.", doc_type=DocType.ADDENDUM, addendum=1)
    qa = [c for c in chunk_document(doc) if c.kind == "qa"]
    assert [c.section for c in qa] == ["Q1", "Q2"]
    assert "2. etching" in qa[0].text


def test_qa_only_detected_in_addenda():
    doc = _doc("1. First numbered point of the RFP body text here.\n2. Second point.")
    assert all(c.kind != "qa" for c in chunk_document(doc))


def test_spec_list_spans_page_break_and_skips_running_header():
    doc = _doc(
        "Tier 3 Student Laptop Minimum Requirements\n• Intel i5 13th Gen\n• Display: 13 inch",
        "SCOPE AND SPECIFICATIONS OF THE\nPROPOSAL\n• Memory: Minimum 16GB RAM DDR5 Memory\n"
        "• Warranty: 3-year minimum\n\nOther text follows here in a new paragraph.",
    )
    [spec] = [c for c in chunk_document(doc) if c.kind == "spec_list"]
    assert spec.section == "Tier 3 Student Laptop Minimum Requirements"
    assert spec.page == 1
    assert "16GB RAM DDR5" in spec.text and "3-year minimum" in spec.text
    assert "SCOPE AND SPECIFICATIONS" not in spec.text
    assert "Other text follows" not in spec.text


def test_real_table_rows_and_layout_grid_skipped(small_chunks):
    real = [["Item", "Quantity"], ["Laptop", "100"], ["Monitor", "25"]]
    layout = [["Section 1", "", ""], ["", "", "Long paragraph only"], ["Other", "", ""]]
    doc = _doc("Page one body text that is long enough to keep around.",
               tables={1: [real, layout]})
    tables = [c for c in chunk_document(doc) if c.kind == "table"]
    assert [t.text for t in tables] == ["Item | Quantity\nLaptop | 100\nMonitor | 25"]


def test_long_table_repeats_header_row(small_chunks):
    rows = [["Description", "Value"]] + [[f"Line item number {i}", f"{i * 10} units"] for i in range(30)]
    doc = _doc("Body text for the page.", tables={1: [rows]})
    tables = [c for c in chunk_document(doc) if c.kind == "table"]
    assert len(tables) > 1
    assert all(t.text.startswith("Description | Value\n") for t in tables)
    assert all(t.token_count <= 60 + 5 for t in tables)


def test_bid_page_is_one_record_even_if_small():
    doc = _doc("Title: Laptops\nClosing Date: 03/03/2025", doc_type=DocType.BID_PAGE, name="p.html")
    [record] = chunk_document(doc)
    assert record.kind == "record" and record.text.startswith("Title: Laptops")


def test_text_packing_respects_max_tokens_with_overlap(small_chunks):
    sentences = " ".join(f"Sentence number {i} describes the delivery terms in detail." for i in range(40))
    chunks = chunk_document(_doc(sentences))
    assert len(chunks) > 3
    assert all(c.kind == "text" and c.token_count <= 60 for c in chunks)
    first_tail = chunks[0].text.split()[-3:]
    assert " ".join(first_tail) in chunks[1].text      # overlap carried over


def test_tiny_document_is_dropped():
    assert chunk_document(_doc("SIGN")) == []


def test_small_section_merges_instead_of_vanishing(small_chunks):
    doc = _doc("1 Tier 1 Small Student 1 Each\n2 Tier 2 Large Student 1 Each\n"
               "LENGTH OF CONTRACT\nThe term is three years with two renewals.")
    text = "\n".join(c.text for c in chunk_document(doc))
    assert "Tier 1 Small Student" in text and "three years" in text


def test_header_ids_and_counts(small_chunks):
    doc = _doc("Body text long enough to make one chunk of text for testing headers.",
               doc_type=DocType.ADDENDUM, addendum=2, name="add2.pdf")
    [chunk] = chunk_document(doc)
    assert chunk.header == "BidX | addendum 2 | add2.pdf | p1 | "
    assert chunk.chunk_id == "d1:1:0"
    assert chunk.token_count == count_tokens(chunk.text)
    assert embed_text(chunk) == chunk.header + "\n" + chunk.text


def test_mini_bid_chunks(mini_bid, small_chunks):
    chunks = [c for d in load_bid_folder(mini_bid) for c in chunk_document(d)]
    kinds = {c.kind for c in chunks}
    assert {"record", "table"} <= kinds
    assert len({c.chunk_id for c in chunks}) == len(chunks)
