"""Cross-encoder re-ranking."""

from __future__ import annotations

from functools import cached_property

from rfp_intel.config import settings
from rfp_intel.search.embedder import configure_torch, load_cached


class Reranker:
    """Scores (query, passage) pairs with a cross-encoder (loaded on first use)."""

    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = model_name or settings.reranker.model

    @cached_property
    def _model(self):  # noqa: ANN202 - CrossEncoder, imported lazily (slow import)
        """The cross-encoder model."""
        from sentence_transformers import CrossEncoder

        return load_cached(CrossEncoder, self.model_name)

    def score(self, query: str, passages: list[str]) -> list[float]:
        """Relevance score per passage (higher is better)."""
        return self.score_pairs([(query, p) for p in passages])

    def score_pairs(self, pairs: list[tuple[str, str]]) -> list[float]:
        """Relevance score per (query, passage) pair in one model call."""
        if not pairs:
            return []
        configure_torch()
        scores = self._model.predict(pairs, show_progress_bar=False)
        return [float(s) for s in scores]
