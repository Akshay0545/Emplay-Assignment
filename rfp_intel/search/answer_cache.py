"""Semantic answer cache (BUILD_PLAN Phase 9, bonus 5).

Answers are stored in a second Qdrant collection keyed by the question's
embedding. A lookup is a hit when the nearest stored question has the same
bid scope, cosine similarity >= `cache.similarity_threshold`, and the bids'
index state has not changed since the answer was produced (so re-indexing a
bid invalidates its cached answers). "Not found" answers are never cached:
they may be the result of a transient failure.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from qdrant_client import models

from rfp_intel.config import settings
from rfp_intel.schemas import Answer

NOT_FOUND = "Not found in documents"


class AnswerCache:
    """Cached answers in Qdrant collection `<chunks collection>_answers`."""

    def __init__(self, client: Any, embedder: Any, dim: int, collection: str | None = None) -> None:
        self.client = client
        self.embedder = embedder
        self.collection = collection or f"{settings.vector_store.collection}_answers"
        if not client.collection_exists(self.collection):
            client.create_collection(
                self.collection, vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE)
            )

    def get(self, question: str, bids: list[str], fingerprint: str) -> Answer | None:
        """The cached answer for an equivalent question in the same scope, or None."""
        vector = self.embedder.embed_queries([question])[0]
        scope = _scope(bids)
        response = self.client.query_points(
            self.collection, query=vector, limit=1, with_payload=True,
            query_filter=models.Filter(must=[
                models.FieldCondition(key="scope", match=models.MatchValue(value=scope)),
                models.FieldCondition(key="fingerprint", match=models.MatchValue(value=fingerprint)),
            ]),
        )
        if not response.points:
            return None
        point = response.points[0]
        if float(point.score) < settings.cache.similarity_threshold:
            return None
        answer = Answer.model_validate(point.payload["answer"])
        return answer.model_copy(update={"question": question, "cached": True})

    def put(self, question: str, bids: list[str], fingerprint: str, answer: Answer) -> None:
        """Store an answer (no-op for "Not found")."""
        if answer.answer.strip().lower().startswith(NOT_FOUND.lower()):
            return
        vector = self.embedder.embed_queries([question])[0]
        scope = _scope(bids)
        payload = {
            "question": question, "scope": scope, "fingerprint": fingerprint,
            "answer": answer.model_copy(update={"cached": False}).model_dump(mode="json"),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{scope}|{fingerprint}|{question.strip().lower()}"))
        self.client.upsert(self.collection, points=[models.PointStruct(id=point_id, vector=vector, payload=payload)])

    def count(self) -> int:
        """Number of cached answers."""
        return int(self.client.count(self.collection, exact=True).count)

    def clear(self) -> int:
        """Delete every cached answer; returns how many there were."""
        n = self.count()
        self.client.delete(self.collection, points_selector=models.FilterSelector(filter=models.Filter(must=[])))
        return n


def index_fingerprint(manifest: dict, bids: list[str]) -> str:
    """Hash of the manifest entries of `bids` (all indexed bids when `bids` is empty)."""
    chosen = sorted(bids) if bids else sorted(k for k in manifest if not str(k).startswith("_"))
    state = {bid: manifest.get(bid) for bid in chosen}
    return hashlib.sha1(json.dumps(state, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _scope(bids: list[str]) -> str:
    """Canonical scope key: sorted bid ids joined with ','; '*' for every bid."""
    return ",".join(sorted(bids)) if bids else "*"
