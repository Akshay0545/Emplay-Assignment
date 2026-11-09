"""Text cleaning: repeated headers/footers, filler lines, hyphenation, whitespace."""

from __future__ import annotations

import math
import re
from collections import Counter
from functools import lru_cache

from rfp_intel.config import load_ingestion
from rfp_intel.schemas import Page

_MIN_REPEATED_LEN = 4   # shorter lines ("Yes", "1") are never treated as headers


def clean_pages(pages: list[Page]) -> list[Page]:
    """Return cleaned copies of `pages` (tables and flags unchanged)."""
    repeated = _repeated_lines(pages)
    return [page.model_copy(update={"text": _clean_text(page.text, repeated)}) for page in pages]


def _repeated_lines(pages: list[Page]) -> set[str]:
    """Line keys that appear on >= 50% of non-empty pages (and on at least 2 pages)."""
    texts = [p.text for p in pages if not p.is_empty]
    if len(texts) < 2:
        return set()
    counts: Counter[str] = Counter()
    for text in texts:
        counts.update({_repeat_key(line) for line in text.splitlines() if _norm(line)})
    threshold = max(2, math.ceil(0.5 * len(texts)))
    return {
        line for line, n in counts.items()
        if n >= threshold and len(line) >= _MIN_REPEATED_LEN and re.search(r"[A-Za-z]", line)
    }


def _clean_text(text: str, repeated: set[str]) -> str:
    """Drop repeated/filler lines, join hyphenated breaks, normalise whitespace."""
    fillers = _filler_patterns()
    kept: list[str] = []
    for line in text.splitlines():
        norm = _norm(line)
        if _repeat_key(line) in repeated or any(p.search(norm) for p in fillers):
            continue
        kept.append(norm)
    joined = "\n".join(kept)
    joined = re.sub(r"([a-z])-\n([a-z])", r"\1\2", joined)   # "procure-\nment" -> "procurement"
    joined = re.sub(r"\n{3,}", "\n\n", joined)               # keep single paragraph breaks
    return joined.strip()


def _repeat_key(line: str) -> str:
    """Line identity for header/footer detection: digits are interchangeable,
    so "Page 3 of 40" and "Page 4 of 40" count as the same footer."""
    return re.sub(r"\d+", "#", _norm(line))


def _norm(line: str) -> str:
    """Line with internal whitespace collapsed."""
    return re.sub(r"[ \t\u00a0\u2000-\u200b]+", " ", line).strip()


@lru_cache(maxsize=1)
def _filler_patterns() -> tuple[re.Pattern[str], ...]:
    """Compiled filler regexes from config/ingestion.yaml."""
    return tuple(re.compile(p, re.IGNORECASE) for p in load_ingestion().filler_patterns)
