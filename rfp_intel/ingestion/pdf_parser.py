"""PDF parsing: text per page (PyMuPDF), tables (pdfplumber), empty-page detection, OCR.

Text lines are rebuilt from PyMuPDF spans grouped by row. A space is inserted
wherever two spans on the same row are separated by a visible gap, so
side-by-side columns ("210-ABCD" | "Widget Base Unit") do not run together.
A larger vertical gap between rows becomes a blank line (paragraph break).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import pdfplumber
import pymupdf

from rfp_intel.config import settings
from rfp_intel.schemas import Page

logger = logging.getLogger(__name__)

_GAP_FACTOR = 0.15        # horizontal gap (x font size) that separates two spans
_PARAGRAPH_FACTOR = 0.8   # vertical gap (x line height) that starts a new paragraph
_OCR_DPI = 300


@dataclass
class _Span:
    """A run of text with its bounding box."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float

    @property
    def y_mid(self) -> float:
        """Vertical centre."""
        return (self.y0 + self.y1) / 2


def parse_pdf(path: Path, warnings: list[str] | None = None) -> list[Page]:
    """Parse a PDF into pages of text and tables.

    Problems that do not stop parsing (empty pages, OCR unavailable, table
    extraction errors) are appended to `warnings`. An unreadable or
    password-protected file raises, and the loader turns that into a warning.
    """
    warnings = warnings if warnings is not None else []
    path = Path(path)
    with pymupdf.open(path) as doc:
        if doc.needs_pass and not doc.authenticate(""):
            raise ValueError("PDF is password-protected")
        texts = [_page_text(page) for page in doc]
        texts = _ocr_empty_pages(doc, texts, warnings)
    tables = _extract_tables(path, len(texts), warnings)
    pages = [
        Page(number=i + 1, text=text, tables=tables[i], is_empty=_is_empty(text))
        for i, text in enumerate(texts)
    ]
    empty = [p.number for p in pages if p.is_empty]
    if empty:
        warnings.append(
            f"{len(empty)} empty page(s) with < {settings.ingestion.min_page_chars} chars: "
            f"{_ranges(empty)}"
        )
    return pages


def _is_empty(text: str) -> bool:
    """True if a page has too little text to be useful."""
    return len(text.strip()) < settings.ingestion.min_page_chars


def _page_text(page: pymupdf.Page) -> str:
    """Page text rebuilt row by row from spans."""
    rows = _group_rows(_spans(page))
    lines: list[str] = []
    prev_bottom: float | None = None
    for row in rows:
        top = min(s.y0 for s in row)
        height = max(s.y1 - s.y0 for s in row) or 1.0
        if prev_bottom is not None and top - prev_bottom > _PARAGRAPH_FACTOR * height:
            lines.append("")
        lines.append(_join_row(row))
        prev_bottom = max(s.y1 for s in row)
    return "\n".join(lines).strip()


def _spans(page: pymupdf.Page) -> list[_Span]:
    """All non-blank text spans on a page."""
    spans: list[_Span] = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                if span["text"].strip():
                    x0, y0, x1, y1 = span["bbox"]
                    spans.append(_Span(span["text"], x0, y0, x1, y1, span["size"]))
    return spans


def _group_rows(spans: list[_Span]) -> list[list[_Span]]:
    """Group spans whose vertical centres are close into rows, top to bottom."""
    rows: list[list[_Span]] = []
    for span in sorted(spans, key=lambda s: (round(s.y_mid, 1), s.x0)):
        if rows:
            row = rows[-1]
            row_mid = sum(s.y_mid for s in row) / len(row)
            tolerance = max(2.0, 0.4 * (span.y1 - span.y0))
            if abs(span.y_mid - row_mid) <= tolerance:
                row.append(span)
                continue
        rows.append([span])
    return [sorted(row, key=lambda s: s.x0) for row in rows]


def _join_row(row: list[_Span]) -> str:
    """Join a row's spans left to right, adding a space at visible gaps."""
    text = row[0].text
    for prev, span in zip(row, row[1:]):
        gap = span.x0 - prev.x1
        needs_space = gap > _GAP_FACTOR * max(prev.size, span.size)
        if needs_space and not text.endswith(" ") and not span.text.startswith(" "):
            text += " "
        text += span.text
    return text.rstrip()


def _ocr_empty_pages(doc: pymupdf.Document, texts: list[str], warnings: list[str]) -> list[str]:
    """Replace empty pages with OCR text when OCR is enabled and available."""
    ocr = settings.ingestion.ocr
    empty = [i for i, text in enumerate(texts) if _is_empty(text)]
    if not empty or not ocr.enabled:
        return texts
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        warnings.append("OCR enabled but pytesseract/Pillow not installed; empty pages kept empty")
        return texts
    out = list(texts)
    for i in empty:
        try:
            pix = doc[i].get_pixmap(dpi=_OCR_DPI)
            image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            out[i] = pytesseract.image_to_string(image, lang=ocr.lang).strip()
        except Exception as exc:  # noqa: BLE001 - missing tesseract binary etc.
            warnings.append(f"OCR failed on page {i + 1}: {type(exc).__name__}: {exc}")
            break
    return out


def _extract_tables(path: Path, page_count: int, warnings: list[str]) -> list[list[list[list[str]]]]:
    """Tables per page with >= 2 rows and >= 2 columns; cells are cleaned text."""
    tables: list[list[list[list[str]]]] = [[] for _ in range(page_count)]
    try:
        with pdfplumber.open(path, password="") as pdf:
            for i, page in enumerate(pdf.pages[:page_count]):
                for raw in page.extract_tables():
                    table = _clean_table(raw)
                    if len(table) >= 2 and max(len(r) for r in table) >= 2:
                        tables[i].append(table)
    except Exception as exc:  # noqa: BLE001 - tables are optional, text still usable
        warnings.append(f"table extraction failed: {type(exc).__name__}: {exc}")
    return tables


def _clean_table(raw: list[list[str | None]]) -> list[list[str]]:
    """Normalise cell whitespace and drop empty rows."""
    rows = [[re.sub(r"\s+", " ", cell or "").strip() for cell in row] for row in raw]
    return [row for row in rows if any(row)]


def _ranges(numbers: list[int]) -> str:
    """Compact page list: [1, 2, 3, 7] -> '1-3, 7'."""
    parts: list[str] = []
    start = prev = numbers[0]
    for n in numbers[1:] + [None]:  # type: ignore[list-item]
        if n is not None and n == prev + 1:
            prev = n
            continue
        parts.append(str(start) if start == prev else f"{start}-{prev}")
        if n is not None:
            start = prev = n
    return ", ".join(parts)
