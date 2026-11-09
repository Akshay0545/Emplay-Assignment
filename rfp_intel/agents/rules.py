"""Deterministic validator rules (no LLM): citations, quotes, formats, consistency.

Used by the Validator node; kept separate so they are unit-testable and so the
LLM support check in the node stays small.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from dateutil import parser as date_parser

from rfp_intel.schemas import Chunk, Citation, FieldValue

DATE_FIELDS = {"Due Date", "Pre Bid Meeting"}      # Delivery Date is a window/text ("within 45 days of award")
CONTACT_FIELDS = {"contact_info"}
ID_FIELDS = {"Model_no", "Part_no"}

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATE_TEXT = re.compile(
    rf"\b(?:\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}[/-]\d{{1,2}}[/-]\d{{2,4}}|\d{{1,2}}-{_MONTH}-\d{{2,4}}"
    rf"|{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}|\d{{1,2}}\s+{_MONTH}\s+\d{{4}})\b",
    re.IGNORECASE,
)
_TIME = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?\b|\b(\d{1,2}):(\d{2})\b(?!\s*[ap]m)", re.IGNORECASE)
_TZ = re.compile(r"\b(utc|gmt|est|edt|et|cst|cdt|ct|mst|mdt|mt|pst|pdt|pt|hst|akst|akdt"
                 r"|eastern|central|mountain|pacific)(?:\s+time)?\b", re.IGNORECASE)
_TZ_OFFSETS_H = {
    "utc": 0, "gmt": 0, "est": -5, "edt": -4, "et": -5, "eastern": -5, "cst": -6, "cdt": -5, "ct": -6,
    "central": -6, "mst": -7, "mdt": -6, "mt": -7, "mountain": -7, "pst": -8, "pdt": -7, "pt": -8,
    "pacific": -8, "hst": -10, "akst": -9, "akdt": -8,
}
_TZ_LABELS = {"eastern": "ET", "central": "CT", "mountain": "MT", "pacific": "PT"}
_NOT_FOUND_PHRASES = {"none", "n/a", "not found", "not found in documents", "not stated", "not specified"}


# ---------------------------------------------------------------- rule checks


def has_citation(value: FieldValue) -> bool:
    """A non-null value needs at least one citation; null values pass."""
    return value.value is None or bool(value.sources)


def quote_in_chunk(citation: Citation, chunks: dict[str, Chunk]) -> bool:
    """The quote must occur in the cited chunk (whitespace-normalized substring)."""
    chunk = chunks.get(citation.chunk_id)
    if chunk is None or not citation.quote.strip():
        return False
    return _squash(citation.quote) in _squash(chunk.text)


def all_quotes_in_chunks(value: FieldValue, chunks: dict[str, Chunk]) -> bool:
    """Every citation of the value passes `quote_in_chunk` (null values pass)."""
    if value.value is None:
        return True
    return all(quote_in_chunk(c, chunks) for c in value.sources)


def format_ok(field: str, value: FieldValue) -> bool:
    """Dates parse with dateutil; contact fields contain an email or phone number."""
    if value.value is None:
        return True
    text = value_text(value)
    if field in DATE_FIELDS:
        return _is_none_phrase(text) or parse_datetime(text) is not None
    if field in CONTACT_FIELDS:
        return bool(_EMAIL.search(text) or _PHONE.search(text))
    return True


def consistent(field: str, drafts: dict[str, FieldValue], evidence_texts: list[str] | None = None) -> bool:
    """Cross-field checks for one field (True when nothing applies).

    - Due Date after the issue/publication date (when the record has one).
    - Pre Bid Meeting before the Due Date.
    - Model_no / Part_no values appear in the Product value or in evidence.
    Time zones are normalized to UTC before comparing.
    """
    value = drafts.get(field)
    if value is None or value.value is None:
        return True
    if field == "Due Date":
        issued = _date_of(drafts.get("Publication Date")) or _date_of(drafts.get("Issue Date"))
        due = parse_datetime(value_text(value))
        return issued is None or due is None or due >= issued
    if field == "Pre Bid Meeting":
        meeting, due = parse_datetime(value_text(value)), _date_of(drafts.get("Due Date"))
        return meeting is None or due is None or meeting <= due
    if field in ID_FIELDS:
        haystack = _squash(value_text(drafts.get("Product")) + " " + " ".join(evidence_texts or []))
        ids = [_squash(v) for v in _as_list(value.value) if _squash(v)]
        return all(i in haystack for i in ids) if ids else True
    return True


# ---------------------------------------------------------------- helpers


def value_text(value: FieldValue | None) -> str:
    """A field value as one string (lists joined with '; ')."""
    if value is None or value.value is None:
        return ""
    return "; ".join(_as_list(value.value))


def parse_datetime(text: str) -> datetime | None:
    """Parse the first date (plus time and zone if present) in free text, as UTC.

    Dates without a time are set to 00:00; without a zone they are treated as
    UTC, so comparisons between zoned and unzoned values stay conservative.
    """
    parts = _datetime_parts(text)
    if parts is None:
        return None
    local, _has_time, zone = parts
    offset = _TZ_OFFSETS_H.get(zone.lower(), 0) if zone else 0
    return (local - timedelta(hours=offset)).replace(tzinfo=timezone.utc)


def format_datetime(text: str) -> str | None:
    """The first date in free text as "YYYY-MM-DD HH:MM TZ" in its own local time.

    The time and zone are only included when the text states them, so a date
    without a time stays "YYYY-MM-DD". Returns None when no date is found.
    """
    parts = _datetime_parts(text)
    if parts is None:
        return None
    local, has_time, zone = parts
    if not has_time:
        return local.strftime("%Y-%m-%d")
    stamp = local.strftime("%Y-%m-%d %H:%M")
    return f"{stamp} {_zone_label(zone)}" if zone else stamp


def _datetime_parts(text: str) -> tuple[datetime, bool, str] | None:
    """(naive local datetime, whether a time was stated, zone token or "") for the first date."""
    date_match = _DATE_TEXT.search(text)
    if not date_match:
        return None
    try:
        day = date_parser.parse(date_match.group(0), fuzzy=False)
    except (ValueError, OverflowError):
        return None
    after = text[date_match.end():date_match.end() + 60]
    before = text[max(0, date_match.start() - 60):date_match.start()]
    time_found = _time_in(after) or _time_in(before)
    hour, minute = time_found or (0, 0)
    zone = _zone_token(after) or _zone_token(before) or _zone_token(text)
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0), time_found is not None, zone


def _time_in(text: str) -> tuple[int, int] | None:
    """Hour and minute mentioned in `text` (24h), or None."""
    match = _TIME.search(text)
    if not match:
        return None
    if match.group(3):                                  # 2:00 PM / 2 pm
        hour, minute = int(match.group(1)), int(match.group(2) or 0)
        if match.group(3).lower() == "p" and hour < 12:
            hour += 12
        if match.group(3).lower() == "a" and hour == 12:
            hour = 0
        return hour, minute
    return int(match.group(4)), int(match.group(5))     # 14:00


def _zone_token(text: str) -> str:
    """The first time-zone word in `text` as written, or ""."""
    match = _TZ.search(text)
    return match.group(1) if match else ""


def _zone_label(zone: str) -> str:
    """Zone abbreviation for output: "Central" -> "CT", "cst" -> "CST"."""
    return _TZ_LABELS.get(zone.lower(), zone.upper())


def _date_of(value: FieldValue | None) -> datetime | None:
    return parse_datetime(value_text(value)) if value and value.value is not None else None


def _as_list(value: str | list[str]) -> list[str]:
    return value if isinstance(value, list) else [value]


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _is_none_phrase(text: str) -> bool:
    return text.strip().lower().rstrip(".") in _NOT_FOUND_PHRASES
