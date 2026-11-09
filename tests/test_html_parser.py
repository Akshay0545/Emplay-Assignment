"""Tests for rfp_intel.ingestion.html_parser."""

from rfp_intel.ingestion.html_parser import parse_html


def test_label_values_extracted_inside_form_wrapper(mini_bid):
    pages, fields = parse_html(mini_bid / "bid_page.html")
    assert fields["Solicitation Number"] == "RFP-2025-001"
    assert fields["Title"] == "Laptops and Monitors"
    assert fields["Closing Date"] == "03/03/2025 02:00 PM CST"
    assert len(pages) == 1 and pages[0].number == 1


def test_value_stops_at_next_labelled_field(mini_bid):
    _, fields = parse_html(mini_bid / "bid_page.html")
    assert fields["Contact Information"] == "Pat Buyer buyer@example.org"
    assert fields["Description"] == "Laptops for staff. Monitors for labs."


def test_dt_dd_and_th_td_pairs(mini_bid):
    _, fields = parse_html(mini_bid / "bid_page.html")
    assert fields["Delivery"] == "Within 30 days of award"
    assert fields["Bond"] == "None required"


def test_page_text_is_label_value_lines_without_chrome(mini_bid):
    pages, _ = parse_html(mini_bid / "bid_page.html")
    text = pages[0].text
    assert "Closing Date: 03/03/2025 02:00 PM CST" in text.splitlines()
    for noise in ("Logout", "Login", "cookie", "Copyright", "tracking", "Open Bids"):
        assert noise.lower() not in text.lower()


def test_missing_labels_are_simply_absent(mini_bid):
    _, fields = parse_html(mini_bid / "bid_page.html")
    assert "Prebid Conference" not in fields


def test_html_without_fields_is_empty_with_warning(tmp_path):
    path = tmp_path / "page.html"
    path.write_text("<html><body><nav>Logout</nav><p>Hi</p></body></html>", encoding="utf-8")
    warnings: list[str] = []
    pages, fields = parse_html(path, warnings)
    assert fields == {} and pages[0].is_empty
    assert warnings == ["no label/value fields found in HTML page"]


def test_invalid_bytes_are_replaced_not_fatal(tmp_path):
    path = tmp_path / "bad.html"
    path.write_bytes(b"<p><span>Title</span><b>Caf\xe9 \xff Laptops</b></p>")
    _, fields = parse_html(path)
    assert fields["Title"].startswith("Caf")
