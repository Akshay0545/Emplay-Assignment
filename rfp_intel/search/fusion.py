"""Reciprocal Rank Fusion."""

from __future__ import annotations

from collections import defaultdict


def rrf(ranked_lists: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    """Fuse ranked id lists: score(id) = sum over lists of 1 / (k + rank), rank from 1.

    Returns (id, score) best first; ties keep first-seen order.
    """
    scores: dict[str, float] = defaultdict(float)
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked, start=1):
            scores[item] += 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
