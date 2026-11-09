"""Tests for rfp_intel.ingestion.classifier."""

import pytest

from rfp_intel.ingestion.classifier import classify, extract_doc_date
from rfp_intel.ingestion.loader import load_bid_folder
from rfp_intel.schemas import DocType


@pytest.mark.parametrize(
    ("file_name", "text", "expected"),
    [
        ("Addendum 2 RFP 123 Laptops.pdf", "", (DocType.ADDENDUM, 2)),
        ("Addendum_No._3.pdf", "", (DocType.ADDENDUM, 3)),
        ("notice.pdf", "ADDENDUM No. 4\nThe due date changes.", (DocType.ADDENDUM, 4)),
        ("Contract_Affidavit.pdf", "", (DocType.AFFIDAVIT, None)),
        ("Laptop_Specs.pdf", "", (DocType.SPECS, None)),
        ("quote.pdf", "SKU Description\n210-ABCD Base", (DocType.SPECS, None)),
        ("PORFP_-_Laptops_Final.pdf", "", (DocType.RFP, None)),
        ("solicitation.pdf", "Request for Proposal 42", (DocType.RFP, None)),
        ("vendor_W9.pdf", "", (DocType.FORM, None)),
        ("misc.pdf", "Hello world", (DocType.OTHER, None)),
    ],
)
def test_classify_pdf(file_name, text, expected):
    assert classify(file_name, text, is_html=False) == expected


def test_html_is_bid_page():
    assert classify("anything.html", "Addendum No. 1", is_html=True) == (DocType.BID_PAGE, None)


def test_file_name_wins_over_text():
    assert classify("Mercury_Affidavit.pdf", "see Addendum No. 1", False) == (DocType.AFFIDAVIT, None)


def test_mini_bid_types(mini_bid):
    types = {d.file_name: (d.doc_type, d.addendum_number) for d in load_bid_folder(mini_bid)}
    assert types == {
        "bid_page.html": (DocType.BID_PAGE, None),
        "notice_update.pdf": (DocType.ADDENDUM, 1),
        "rfp.pdf": (DocType.RFP, None),
    }


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Issue Date: January 15, 2025\nProposals due March 3, 2025", "2025-01-15"),
        ("Header\nDate: 07/09/2024\nBody", "2024-07-09"),
        ("Opened 27-JUN-2024 at noon", "2024-06-27"),
        ("Published 2024-05-28 by the office", "2024-05-28"),
        ("Proposals are due March 3, 2025 at 2:00 PM.", None),
        ("Closing Date: 03/03/2025 02:00 PM", None),
        ("The due date is extended to July 9, 2024. Issued 1 June 2024", "2024-06-01"),
        ("No dates here at all", None),
    ],
)
def test_extract_doc_date(text, expected):
    assert extract_doc_date(text) == expected


def test_date_after_lead_window_is_ignored():
    assert extract_doc_date("x" * 600 + " March 3, 2025") is None
