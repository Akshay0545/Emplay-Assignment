"""Check the eval data against the real bid files (Phase 1 acceptance).

1. Every retrieval question's key_phrase occurs in its expected_file.
2. Every gold `must_contain` token occurs somewhere in that bid's files.

Reads files directly with PyMuPDF / BeautifulSoup (independent of the
ingestion pipeline). Matching ignores case and whitespace.

    python -m eval.verify_eval_data
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

import pymupdf
from bs4 import BeautifulSoup
from rich.console import Console
from rich.table import Table

from eval.loaders import contains_phrase, gold_bid_ids, load_gold, load_retrieval_questions
from rfp_intel.config import settings

console = Console()


@lru_cache(maxsize=None)
def file_text(path: Path) -> str:
    """Raw text of a PDF or HTML file."""
    if path.suffix.lower() == ".pdf":
        with pymupdf.open(path) as doc:
            return "\n".join(page.get_text("text") for page in doc)
    html = path.read_text(encoding="utf-8", errors="replace")
    return BeautifulSoup(html, "lxml").get_text(" ")


def bid_files(bid_id: str) -> list[Path]:
    """PDF and HTML files of one bid folder."""
    folder = settings.path("data_dir") / bid_id
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in {".pdf", ".html", ".htm"})


def check_key_phrases() -> int:
    """Print the key-phrase table; return the number of failures."""
    table = Table(title="Retrieval questions: key phrase in expected file")
    for col in ("id", "bid", "expected_file", "key_phrase", "result"):
        table.add_column(col)
    failures = 0
    for q in load_retrieval_questions():
        path = settings.path("data_dir") / q.bid_id / q.expected_file
        if not path.exists():
            ok, note = False, "FAIL (file missing)"
        else:
            ok = contains_phrase(file_text(path), q.key_phrase)
            note = "PASS" if ok else "FAIL"
        failures += not ok
        table.add_row(str(q.id), q.bid_id, q.expected_file[:45], q.key_phrase, note)
    console.print(table)
    return failures


def check_gold_tokens() -> int:
    """Print the gold-token table; return the number of failures."""
    table = Table(title="Gold must_contain tokens found in bid files")
    for col in ("bid", "field", "token", "result"):
        table.add_column(col)
    failures = 0
    for bid_id in gold_bid_ids():
        corpus = "\n".join(file_text(p) for p in bid_files(bid_id))
        for field, gold in load_gold(bid_id).items():
            for token in gold.must_contain:
                ok = contains_phrase(corpus, token)
                failures += not ok
                table.add_row(bid_id, field, token, "PASS" if ok else "FAIL")
    console.print(table)
    return failures


def main() -> int:
    """Run both checks; exit code 1 if anything failed."""
    key_failures = check_key_phrases()
    token_failures = check_gold_tokens()
    total = len(load_retrieval_questions())
    console.print(f"Key phrases found: {total - key_failures}/{total}")
    console.print(f"Gold token failures: {token_failures}")
    return 1 if key_failures or token_failures else 0


if __name__ == "__main__":
    sys.exit(main())
