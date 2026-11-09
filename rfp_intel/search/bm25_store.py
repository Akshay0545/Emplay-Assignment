"""Keyword search: one BM25 corpus per bid, stored at storage/bm25/<bid>.pkl.

Each pickle holds the bid's chunks and their tokens. A search builds one
BM25Okapi over the chunks that pass the filters (possibly several bids), so
scores are comparable across bids. Corpora are a few hundred chunks, so
building on demand is cheap; built indexes are cached per filter.
"""

from __future__ import annotations

import pickle
import re
from pathlib import Path

from rank_bm25 import BM25Okapi

from rfp_intel.schemas import Chunk, SearchFilters

_TOKEN = re.compile(r"[a-z0-9]+(?:[-#][a-z0-9]+)*#?")
_PART_SPLIT = re.compile(r"[-#]")


def tokenize(text: str) -> list[str]:
    """Lower-case tokens; IDs stay whole ("rfp-2025-001", "ab12cd3", "si#").

    Compound tokens are followed by their parts ("ja", "207652") so a search
    for part of an ID still matches.
    """
    tokens: list[str] = []
    for token in _TOKEN.findall(text.lower()):
        tokens.append(token)
        if _PART_SPLIT.search(token):
            tokens.extend(part for part in _PART_SPLIT.split(token) if part)
    return tokens


class BM25Store:
    """Per-bid BM25 corpora on disk."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self._corpora: dict[str, tuple[list[Chunk], list[list[str]]]] = {}
        self._indexes: dict[tuple, tuple[BM25Okapi, list[Chunk], list[set[str]]]] = {}

    def save(self, bid_id: str, chunks: list[Chunk], texts: list[str]) -> None:
        """Store a bid's chunks with the texts to index (header + text)."""
        self.directory.mkdir(parents=True, exist_ok=True)
        tokens = [tokenize(text) for text in texts]
        payload = {"chunks": [c.model_dump(mode="json") for c in chunks], "tokens": tokens}
        with open(self._path(bid_id), "wb") as fh:
            pickle.dump(payload, fh)
        self._forget(bid_id)

    def delete(self, bid_id: str) -> None:
        """Remove a bid's corpus."""
        self._path(bid_id).unlink(missing_ok=True)
        self._forget(bid_id)

    def bids(self) -> list[str]:
        """Bids with a stored corpus."""
        return sorted(p.stem for p in self.directory.glob("*.pkl"))

    def search(self, query: str, filters: SearchFilters | None, limit: int) -> list[tuple[Chunk, float]]:
        """Top chunks containing at least one query token, by BM25 score.

        Matching is by token presence, not score > 0: in small corpora a term
        found in half the chunks gets an IDF of exactly 0.
        """
        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        index = self._index(filters)
        if index is None:
            return []
        bm25, chunks, token_sets = index
        wanted = set(query_tokens)
        matching = [i for i, tokens in enumerate(token_sets) if wanted & tokens]
        scores = bm25.get_scores(query_tokens)
        matching.sort(key=lambda i: scores[i], reverse=True)
        return [(chunks[i], float(scores[i])) for i in matching[:limit]]

    # -------------------------------------------------------------- internals

    def _path(self, bid_id: str) -> Path:
        return self.directory / f"{bid_id}.pkl"

    def _forget(self, bid_id: str) -> None:
        """Drop cached data that involves a bid."""
        self._corpora.pop(bid_id, None)
        self._indexes = {k: v for k, v in self._indexes.items() if bid_id not in k[0]}

    def _load(self, bid_id: str) -> tuple[list[Chunk], list[list[str]]]:
        """A bid's chunks and tokens (cached)."""
        if bid_id not in self._corpora:
            path = self._path(bid_id)
            if not path.exists():
                return [], []
            with open(path, "rb") as fh:
                payload = pickle.load(fh)
            self._corpora[bid_id] = ([Chunk(**c) for c in payload["chunks"]], payload["tokens"])
        return self._corpora[bid_id]

    def _index(self, filters: SearchFilters | None) -> tuple[BM25Okapi, list[Chunk], list[set[str]]] | None:
        """BM25 over the chunks that pass `filters` (cached per filter)."""
        filters = filters or SearchFilters()
        bids = filters.bid_ids or ([filters.bid_id] if filters.bid_id else self.bids())
        types = tuple(sorted(t.value for t in filters.doc_types)) if filters.doc_types else None
        key = (tuple(sorted(bids)), types, filters.addendum_number)
        if key not in self._indexes:
            chunks: list[Chunk] = []
            tokens: list[list[str]] = []
            for bid in bids:
                for chunk, toks in zip(*self._load(bid)):
                    if _matches(chunk, filters):
                        chunks.append(chunk)
                        tokens.append(toks)
            if not chunks:
                return None
            self._indexes[key] = (BM25Okapi(tokens), chunks, [set(t) for t in tokens])
        return self._indexes[key]


def _matches(chunk: Chunk, filters: SearchFilters) -> bool:
    """Doc-type and addendum filters (bid filters are applied by corpus choice)."""
    if filters.doc_types and chunk.doc_type not in filters.doc_types:
        return False
    if filters.addendum_number is not None and chunk.addendum_number != filters.addendum_number:
        return False
    return True
