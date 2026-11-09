"""Tests for rfp_intel.ingestion.cleaner."""

from rfp_intel.ingestion.cleaner import clean_pages
from rfp_intel.ingestion.loader import load_bid_folder
from rfp_intel.schemas import Page


def _pages(*texts: str) -> list[Page]:
    return [Page(number=i + 1, text=t) for i, t in enumerate(texts)]


def test_repeated_header_removed_and_page_footer_removed():
    pages = _pages(
        "ACME HEADER LINE\nFirst page body.\nPage 1 of 3",
        "ACME HEADER LINE\nSecond page body.\nPage 2 of 3",
        "ACME HEADER LINE\nThird page body.\nPage 3 of 3",
    )
    cleaned = [p.text for p in clean_pages(pages)]
    assert cleaned == ["First page body.", "Second page body.", "Third page body."]


def test_footer_with_changing_numbers_is_repeated():
    pages = _pages(*(f"Body text {i} here.\nAcme rev 1.0 Page {i} of 40" for i in range(1, 5)))
    assert all("Acme rev" not in p.text for p in clean_pages(pages))


def test_short_or_numeric_repeated_lines_are_kept():
    pages = _pages("Yes\n50,000\nalpha body", "Yes\n50,000\nbeta body")
    cleaned = clean_pages(pages)
    assert cleaned[0].text == "Yes\n50,000\nalpha body"


def test_single_page_document_keeps_all_lines():
    [page] = clean_pages(_pages("ADDENDUM No. 2\nBody"))
    assert page.text == "ADDENDUM No. 2\nBody"


def test_filler_lines_removed():
    [page] = clean_pages(_pages(
        "Question one\nProvide your answer below\nType\n..........................\nAnswer"
    ))
    assert page.text == "Question one\nAnswer"


def test_hyphenation_joined_but_ids_kept():
    [page] = clean_pages(_pages("the procure-\nment of devices\nRFP JA-\n207652"))
    assert page.text == "the procurement of devices\nRFP JA-\n207652"


def test_whitespace_collapsed_and_paragraph_breaks_kept():
    [page] = clean_pages(_pages("  a   b\t c \n\n\n\nnext  para  "))
    assert page.text == "a b c\n\nnext para"


def test_tables_and_flags_unchanged():
    page = Page(number=1, text="x", tables=[[["a", "b"], ["c", "d"]]], is_empty=True)
    [cleaned] = clean_pages([page])
    assert cleaned.tables == page.tables and cleaned.is_empty


def test_mini_bid_rfp_is_clean(mini_bid):
    rfp = next(d for d in load_bid_folder(mini_bid) if d.file_name == "rfp.pdf")
    text = "\n".join(p.text for p in rfp.pages)
    assert "EXAMPLE COUNTY PURCHASING DEPARTMENT" not in text
    assert "Page 1 of 2" not in text
    assert "Provide your answer below" not in text
    assert "procurement of laptops" in text
