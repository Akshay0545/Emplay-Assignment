"""Generate tests/fixtures/mini_bid: a tiny synthetic bid folder.

    python tests/fixtures/make_mini_bid.py

Contents (all fictional):
- rfp.pdf: 2 pages, repeated header, "Page N of 2" footer, a ruled table,
  a filler line, a hyphenated line break and two side-by-side columns.
- notice_update.pdf: an addendum identified only by its text ("ADDENDUM No. 1").
- bid_page.html: portal page with nav, cookie banner, a <form> wrapper,
  label/value fields, a dt/dd pair and a th/td pair.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

MINI_BID_DIR = Path(__file__).resolve().parent / "mini_bid"

HEADER = "EXAMPLE COUNTY PURCHASING DEPARTMENT"
TABLE = [["Item", "Quantity"], ["Laptop", "100"], ["Monitor", "25"]]

HTML = """<!doctype html>
<html><head><title>Bid Information</title>
<script>var tracking = "Logout";</script><style>.x { color: red; }</style></head>
<body>
<header><a href="#">Login</a> <a href="#">Logout</a></header>
<nav>Home | Open Bids | Logout</nav>
<div class="cookie-banner">We use cookies to improve your experience. Accept cookies.</div>
<form id="main"><div class="content">
  <div class="field"><span class="label">Solicitation Number</span>
    <div class="value"><p>RFP-2025-001</p></div></div>
  <div class="field"><span class="label">Title</span>
    <div class="value"><p>Laptops and Monitors</p></div></div>
  <div class="field"><span class="label">Closing Date</span>
    <div class="value"><p>03/03/2025 02:00 PM CST</p></div></div>
  <h3>Contact Information</h3>
  <div class="contact"><p>Pat Buyer</p><p>buyer@example.org</p></div>
  <div class="field"><span class="label">Description</span>
    <div class="value"><p>Laptops for staff.</p><p>Monitors for labs.</p></div></div>
  <dl><dt>Delivery</dt><dd>Within 30 days of award</dd></dl>
  <table><tr><th>Bond</th><td>None required</td></tr></table>
</div></form>
<footer>Copyright Example County</footer>
</body></html>
"""


def _text(page: pymupdf.Page, x: float, y: float, text: str, size: float = 11) -> None:
    """Write one line of text with its baseline at (x, y)."""
    page.insert_text((x, y), text, fontsize=size, fontname="helv")


def _table(page: pymupdf.Page, x: float, y: float, rows: list[list[str]]) -> None:
    """Draw a ruled table so pdfplumber can detect it."""
    col_w, row_h = 150, 22
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            rect = pymupdf.Rect(x + c * col_w, y + r * row_h, x + (c + 1) * col_w, y + (r + 1) * row_h)
            page.draw_rect(rect, color=(0, 0, 0), width=0.8)
            _text(page, rect.x0 + 5, rect.y1 - 7, cell)


def _rfp(path: Path) -> None:
    """Two-page RFP with header, footer, table, filler and hyphenation."""
    doc = pymupdf.open()
    for number in (1, 2):
        page = doc.new_page()
        _text(page, 50, 40, HEADER, size=9)
        _text(page, 270, 810, f"Page {number} of 2", size=9)
        if number == 1:
            _text(page, 50, 90, "REQUEST FOR PROPOSAL RFP-2025-001", size=14)
            _text(page, 50, 115, "Issue Date: January 15, 2025")
            _text(page, 50, 135, "Proposals are due March 3, 2025 at 2:00 PM CST.")
            _text(page, 50, 175, "The county seeks the procure-")
            _text(page, 50, 190, "ment of laptops and monitors for staff.")
            _text(page, 50, 230, "Provide your answer below")
            _table(page, 50, 260, TABLE)
        else:
            _text(page, 50, 90, "Technical Requirements", size=13)
            _text(page, 50, 115, "- 16GB RAM")
            _text(page, 50, 130, "- 256GB SSD")
            _text(page, 50, 160, "ABC-123")
            _text(page, 250, 160, "Widget Base Unit")
            _text(page, 50, 200, "Contact: buyer@example.org")
    doc.save(path)


def _addendum(path: Path) -> None:
    """One-page addendum recognisable only from its text."""
    doc = pymupdf.open()
    page = doc.new_page()
    _text(page, 50, 60, "ADDENDUM No. 1", size=14)
    _text(page, 50, 90, "RFP-2025-001 Laptops and Monitors")
    _text(page, 50, 120, "The due date is extended to March 10, 2025 at 2:00 PM CST.")
    doc.save(path)


def build(out_dir: Path = MINI_BID_DIR) -> Path:
    """Write the mini bid files into `out_dir` and return it."""
    out_dir.mkdir(parents=True, exist_ok=True)
    _rfp(out_dir / "rfp.pdf")
    _addendum(out_dir / "notice_update.pdf")
    (out_dir / "bid_page.html").write_text(HTML, encoding="utf-8")
    return out_dir


if __name__ == "__main__":
    print(f"wrote {build()}")
