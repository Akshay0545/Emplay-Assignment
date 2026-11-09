"""Document classification and document-date extraction (patterns from config/ingestion.yaml)."""

from __future__ import annotations

import re
from pathlib import Path

from dateutil import parser as date_parser

from rfp_intel.config import load_ingestion
from rfp_intel.schemas import DocType

_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATE_PATTERNS = [
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),                                  # 2025-01-15
    re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"),                      # 07/09/2024
    re.compile(rf"\b\d{{1,2}}-{_MONTH}-\d{{2,4}}\b", re.IGNORECASE),        # 27-JUN-2024
    re.compile(rf"\b{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}\b", re.IGNORECASE),  # January 15, 2025
    re.compile(rf"\b\d{{1,2}}\s+{_MONTH}\s+\d{{4}}\b", re.IGNORECASE),     # 9 July 2024
]
_DATE_KEYWORD = re.compile(
    r"\bissued?\s+date\b|\bdate\s+issued\b|^\s*date\b", re.IGNORECASE | re.MULTILINE
)
_DEADLINE_CONTEXT = re.compile(r"\b(due|deadline|closing|closes|extend\w*|no later than)\b", re.IGNORECASE)
_DEADLINE_LOOKBACK = 60
_KEYWORD_WINDOW = 80
_LEAD_CHARS = 500


def classify(file_name: str, first_page_text: str, is_html: bool) -> tuple[DocType, int | None]:
    """Doc type and addendum number.

    HTML is always the bid page. Otherwise the file name is checked against
    every pattern first (a strong signal), then the first page's text.
    """
    if is_html:
        return DocType.BID_PAGE, None
    name = re.sub(r"[_\-.]+", " ", Path(file_name).stem)
    for source in (name, first_page_text):
        result = _match(source)
        if result is not None:
            return result
    return DocType.OTHER, None


def _match(text: str) -> tuple[DocType, int | None] | None:
    """First classifier pattern (in config order) that matches `text`."""
    for doc_type, pattern in load_ingestion().classifier.items():
        found = re.search(pattern, text, re.IGNORECASE)
        if found is None:
            continue
        number = None
        if doc_type == DocType.ADDENDUM.value:
            digits = [g for g in found.groups() if g and g.isdigit()]
            number = int(digits[-1]) if digits else None
        return DocType(doc_type), number
    return None


def extract_doc_date(text: str) -> str | None:
    """ISO date near "Issue Date" / a line starting "Date", else the first
    non-deadline date in the first 500 chars."""
    for keyword in _DATE_KEYWORD.finditer(text):
        found = _first_date(text[keyword.end(): keyword.end() + _KEYWORD_WINDOW])
        if found:
            return found
    return _first_date(text[:_LEAD_CHARS], skip_deadlines=True)


def _first_date(text: str, skip_deadlines: bool = False) -> str | None:
    """Earliest parseable date in `text`, as ISO yyyy-mm-dd.

    With `skip_deadlines`, dates just after words like "due" or "closing" are
    ignored: they are deadlines, not the date the document was issued.
    """
    matches = sorted(
        (m for pattern in _DATE_PATTERNS for m in pattern.finditer(text)), key=lambda m: m.start()
    )
    for match in matches:
        if skip_deadlines and _DEADLINE_CONTEXT.search(_same_sentence_before(text, match.start())):
            continue
        try:
            return date_parser.parse(match.group(0), fuzzy=False).date().isoformat()
        except (ValueError, OverflowError):
            continue
    return None


def _same_sentence_before(text: str, end: int) -> str:
    """Up to 60 chars before `end`, cut at the last sentence or line break."""
    before = text[max(0, end - _DEADLINE_LOOKBACK): end]
    cut = max(before.rfind(". "), before.rfind("\n"))
    return before[cut + 1:] if cut >= 0 else before
