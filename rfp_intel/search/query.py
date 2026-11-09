"""Query expansion (synonyms.yaml) and filter parsing from query text.

Filters parsed from text:
- a known bid id ("Bid2") -> bid filter;
- "addendum N" -> addendum N (doc type addendum);
- a doc-type word only when the query scopes the search to it ("only search
  ... specs", "in the addendum", "from the RFP", "specs only"). A bare
  mention ("Which affidavit concerns mercury?") is a topic, not a scope.
"""

from __future__ import annotations

import re

from rfp_intel.config import load_synonyms
from rfp_intel.schemas import DocType, SearchFilters

MAX_QUERY_VARIANTS = 4

_DOC_TYPE_WORDS: dict[DocType, str] = {
    DocType.SPECS: r"spec(?:ification)?s?(?:\s+sheets?)?",
    DocType.ADDENDUM: r"addend(?:um|a)",
    DocType.AFFIDAVIT: r"affidavits?",
    DocType.RFP: r"rfp|request\s+for\s+proposals?",
    DocType.BID_PAGE: r"portal|bid\s+page",
}
_ADDENDUM_NUMBER = re.compile(r"\baddendum\s*(?:no\.?|#|number)?\s*(\d+)\b", re.IGNORECASE)


def expand_query(query: str) -> list[str]:
    """The query plus variants with a synonym substituted for each matched phrase.

    Matching is on whole phrases, case-insensitive; at most MAX_QUERY_VARIANTS
    queries are returned, the original first.
    """
    variants = [query]
    seen = {query.lower()}
    for phrase, synonyms in load_synonyms().items():
        pattern = re.compile(rf"\b{re.escape(phrase)}\b", re.IGNORECASE)
        if not pattern.search(query):
            continue
        for synonym in synonyms:
            variant = pattern.sub(synonym, query, count=1)
            if variant.lower() not in seen:
                variants.append(variant)
                seen.add(variant.lower())
            if len(variants) >= MAX_QUERY_VARIANTS:
                return variants
    return variants


def parse_filters(query: str, known_bids: list[str]) -> tuple[SearchFilters, str]:
    """Filters stated in the query, and the query with scoping words removed."""
    cleaned = query
    bids = [b for b in known_bids if re.search(rf"\b{re.escape(b)}\b", query, re.IGNORECASE)]
    filters = SearchFilters()
    if len(bids) == 1:
        filters.bid_id = bids[0]
    elif bids:
        filters.bid_ids = bids

    addendum = _ADDENDUM_NUMBER.search(query)
    if addendum:
        filters.addendum_number = int(addendum.group(1))
        filters.doc_types = [DocType.ADDENDUM]

    bid_alt = "|".join(re.escape(b) for b in known_bids) or r"(?!x)x"
    for doc_type, words in _DOC_TYPE_WORDS.items():
        if doc_type == DocType.ADDENDUM and addendum:
            continue                       # keep "Addendum N" in the query text
        for scope in _scope_patterns(words, bid_alt):
            match = scope.search(cleaned)
            if match:
                if doc_type not in (filters.doc_types or []):
                    filters.doc_types = (filters.doc_types or []) + [doc_type]
                cleaned = cleaned[:match.start()] + " " + cleaned[match.end():]
                break

    for bid in bids:
        cleaned = re.sub(rf"\b{re.escape(bid)}\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.:;")
    return filters, cleaned or query


def _scope_patterns(words: str, bid_alt: str) -> list[re.Pattern[str]]:
    """Phrasings that restrict a search to a document type."""
    bid = rf"(?:(?:{bid_alt})(?:'s)?\s+)?"
    return [
        re.compile(rf"\bonly\s+(?:search\s+)?(?:in\s+|within\s+)?(?:the\s+)?{bid}(?:{words})\b", re.I),
        re.compile(rf"\b(?:in|within|from)\s+(?:the\s+)?{bid}(?:{words})\b", re.I),
        re.compile(rf"\b{bid}(?:{words})\s+only\b", re.I),
    ]
