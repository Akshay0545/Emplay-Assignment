"""Load a bid folder into Documents.

Per file: sha1 hash, dispatch by extension, classify on the raw first page,
clean, then read the document date and title. One bad file never fails the
run: a file that cannot be parsed becomes a Document with no pages and a
warning; unsupported or duplicate files are skipped with a logged warning.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

import pymupdf

from rfp_intel.ingestion.classifier import classify, extract_doc_date
from rfp_intel.ingestion.cleaner import clean_pages
from rfp_intel.ingestion.html_parser import parse_html
from rfp_intel.ingestion.pdf_parser import parse_pdf
from rfp_intel.schemas import Document, Page

logger = logging.getLogger(__name__)

PDF_SUFFIXES = {".pdf"}
HTML_SUFFIXES = {".html", ".htm"}
SUPPORTED_SUFFIXES = PDF_SUFFIXES | HTML_SUFFIXES
_MAX_TITLE_CHARS = 150
_MAX_TITLE_DIGIT_SHARE = 0.2


def load_bid_folder(folder: Path) -> list[Document]:
    """Parse every supported file in a bid folder (bid_id = folder name)."""
    folder = Path(folder)
    if not folder.is_dir():
        raise NotADirectoryError(f"bid folder not found: {folder}")
    documents: list[Document] = []
    seen: dict[str, str] = {}
    for path in sorted(p for p in folder.iterdir() if p.is_file()):
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            logger.warning("skipping %s: unsupported file type %r", path.name, path.suffix)
            continue
        doc_id = hashlib.sha1(path.read_bytes()).hexdigest()
        if doc_id in seen:
            logger.warning("skipping %s: duplicate of %s", path.name, seen[doc_id])
            continue
        seen[doc_id] = path.name
        documents.append(load_file(path, bid_id=folder.name, doc_id=doc_id))
    return documents


def load_file(path: Path, bid_id: str, doc_id: str) -> Document:
    """Parse, classify and clean one file; never raises for bad content."""
    is_html = path.suffix.lower() in HTML_SUFFIXES
    warnings: list[str] = []
    try:
        if is_html:
            pages, fields = parse_html(path, warnings)
        else:
            pages, fields = parse_pdf(path, warnings), {}
    except Exception as exc:  # noqa: BLE001 - one bad file must not fail the run
        warnings.append(f"could not parse file: {type(exc).__name__}: {exc}")
        doc_type, addendum_number = classify(path.name, "", is_html)
        _log_warnings(path, warnings)
        return Document(doc_id=doc_id, bid_id=bid_id, file_name=path.name, doc_type=doc_type,
                        addendum_number=addendum_number, pages=[], warnings=warnings)

    first_text = next((p.text for p in pages if not p.is_empty), "")
    doc_type, addendum_number = classify(path.name, first_text, is_html)
    if pages and all(p.is_empty for p in pages):
        warnings.append("no extractable text (scanned or empty file)")
    _log_warnings(path, warnings)
    return Document(
        doc_id=doc_id,
        bid_id=bid_id,
        file_name=path.name,
        doc_type=doc_type,
        addendum_number=addendum_number,
        doc_date=extract_doc_date(first_text),
        title=_title(path, pages, fields, is_html),
        pages=clean_pages(pages),
        fields=fields,
        warnings=warnings,
    )


def _title(path: Path, pages: list[Page], fields: dict[str, str], is_html: bool) -> str | None:
    """HTML: the Title field. PDF: meaningful metadata title, else the first
    line with 3+ words that is not mostly digits (addresses, phone numbers)."""
    if is_html:
        return fields.get("Title")
    meta = _pdf_metadata_title(path)
    if meta:
        return meta
    for page in pages:
        for line in page.text.splitlines():
            line = line.strip()
            if len(line.split()) >= 3 and _digit_share(line) <= _MAX_TITLE_DIGIT_SHARE:
                return line[:_MAX_TITLE_CHARS]
    return None


def _digit_share(text: str) -> float:
    """Fraction of characters that are digits."""
    return sum(c.isdigit() for c in text) / max(len(text), 1)


def _pdf_metadata_title(path: Path) -> str | None:
    """PDF metadata title unless it is empty or an editor artefact."""
    try:
        with pymupdf.open(path) as doc:
            title = (doc.metadata or {}).get("title") or ""
    except Exception:  # noqa: BLE001 - metadata is optional
        return None
    title = re.sub(r"^Microsoft (Word|Excel|PowerPoint) - ", "", title).strip()
    title = re.sub(r"\.(docx?|xlsx?|pdf)$", "", title, flags=re.IGNORECASE).strip()
    if len(title) < 5 or title.lower() in {"untitled", path.stem.lower()}:
        return None
    return title[:_MAX_TITLE_CHARS]


def _log_warnings(path: Path, warnings: list[str]) -> None:
    """Log every warning for a file."""
    for warning in warnings:
        logger.warning("%s: %s", path.name, warning)
