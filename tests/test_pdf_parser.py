"""Tests for rfp_intel.ingestion.pdf_parser."""

import pymupdf

from rfp_intel.ingestion.pdf_parser import parse_pdf


def test_pages_text_and_numbers(mini_bid):
    pages = parse_pdf(mini_bid / "rfp.pdf")
    assert [p.number for p in pages] == [1, 2]
    assert "REQUEST FOR PROPOSAL RFP-2025-001" in pages[0].text
    assert "Technical Requirements" in pages[1].text
    assert not any(p.is_empty for p in pages)


def test_ruled_table_is_extracted(mini_bid):
    pages = parse_pdf(mini_bid / "rfp.pdf")
    assert pages[0].tables == [[["Item", "Quantity"], ["Laptop", "100"], ["Monitor", "25"]]]
    assert pages[1].tables == []


def test_side_by_side_columns_get_a_space(mini_bid):
    text = parse_pdf(mini_bid / "rfp.pdf")[1].text
    assert "ABC-123 Widget Base Unit" in text


def test_lines_keep_reading_order(mini_bid):
    lines = parse_pdf(mini_bid / "rfp.pdf")[0].text.splitlines()
    first = lines.index("The county seeks the procure-")
    assert lines[first + 1] == "ment of laptops and monitors for staff."


def test_empty_page_flagged_with_warning(tmp_path):
    path = tmp_path / "blank.pdf"
    doc = pymupdf.open()
    doc.new_page()
    page = doc.new_page()
    page.insert_text((50, 60), "Some real content on the second page.")
    doc.save(path)

    warnings: list[str] = []
    pages = parse_pdf(path, warnings)
    assert [p.is_empty for p in pages] == [True, False]
    assert warnings == ["1 empty page(s) with < 20 chars: 1"]


def test_empty_pages_are_reported_as_ranges(tmp_path):
    path = tmp_path / "blanks.pdf"
    doc = pymupdf.open()
    for _ in range(4):
        doc.new_page()
    doc.save(path)
    warnings: list[str] = []
    parse_pdf(path, warnings)
    assert warnings == ["4 empty page(s) with < 20 chars: 1-4"]
