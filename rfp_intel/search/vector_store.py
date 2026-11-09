"""Qdrant vector store with full Chunk payloads and metadata filters.

`vector_store.kind` picks the client: `qdrant_local` keeps everything in
`storage/qdrant` and allows one client per folder at a time (one process);
`qdrant_server` talks to the server at `QDRANT_URL` (.env) so several API
processes can share one index. Payload indexes are requested for bid_id,
doc_type and addendum_number; local mode ignores them, a server uses them.
"""

from __future__ import annotations

import logging
import uuid
import warnings
from pathlib import Path

from qdrant_client import QdrantClient, models

from rfp_intel.config import settings
from rfp_intel.schemas import Chunk, SearchFilters

logger = logging.getLogger(__name__)

_INDEXED_FIELDS = {
    "bid_id": models.PayloadSchemaType.KEYWORD,
    "doc_type": models.PayloadSchemaType.KEYWORD,
    "addendum_number": models.PayloadSchemaType.INTEGER,
}
_UPSERT_BATCH = 256


def make_client(kind: str, path: Path, url: str = "", api_key: str = "") -> QdrantClient:
    """A Qdrant client for `kind`: embedded at `path`, or the server at `url`."""
    if kind == "qdrant_local":
        Path(path).mkdir(parents=True, exist_ok=True)
        return QdrantClient(path=str(path))
    if kind == "qdrant_server":
        if not url:
            raise ValueError("vector_store.kind is qdrant_server but QDRANT_URL is not set in .env")
        return QdrantClient(url=url, api_key=api_key or None)
    raise ValueError(f"unknown vector_store.kind {kind!r} (use qdrant_local or qdrant_server)")


class VectorStore:
    """Chunks and their vectors in a Qdrant collection."""

    def __init__(self, path: Path, collection: str, dim: int, client: QdrantClient | None = None) -> None:
        env = settings.env
        self.client = client or make_client(settings.vector_store.kind, path, env.qdrant_url,
                                            env.qdrant_api_key.get_secret_value())
        self.collection = collection
        self._ensure_collection(dim)

    def _ensure_collection(self, dim: int) -> None:
        """Create the collection and payload indexes if missing."""
        if self.client.collection_exists(self.collection):
            return
        self.client.create_collection(
            self.collection,
            vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE),
        )
        for name, schema in _INDEXED_FIELDS.items():
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")   # local mode: "indexes have no effect"
                    self.client.create_payload_index(self.collection, name, field_schema=schema)
            except Exception as exc:  # noqa: BLE001 - optional optimisation
                logger.debug("payload index %s not created: %s", name, exc)

    def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        """Insert or replace chunks with their vectors."""
        points = [
            models.PointStruct(id=point_id(c), vector=v, payload=c.model_dump(mode="json"))
            for c, v in zip(chunks, vectors, strict=True)
        ]
        for i in range(0, len(points), _UPSERT_BATCH):
            self.client.upsert(self.collection, points=points[i:i + _UPSERT_BATCH])

    def delete_by_bid(self, bid_id: str) -> None:
        """Remove every chunk of a bid."""
        self.client.delete(
            self.collection,
            points_selector=models.FilterSelector(filter=to_qdrant_filter(SearchFilters(bid_id=bid_id))),
        )

    def count(self, bid_id: str | None = None) -> int:
        """Number of stored chunks (optionally for one bid)."""
        flt = to_qdrant_filter(SearchFilters(bid_id=bid_id)) if bid_id else None
        return self.client.count(self.collection, count_filter=flt, exact=True).count

    def search(self, vector: list[float], filters: SearchFilters | None,
               limit: int) -> list[tuple[Chunk, float]]:
        """Nearest chunks to `vector` that match `filters`, best first."""
        response = self.client.query_points(
            self.collection, query=vector, query_filter=to_qdrant_filter(filters),
            limit=limit, with_payload=True,
        )
        return [(Chunk(**point.payload), float(point.score)) for point in response.points]

    def close(self) -> None:
        """Release the local storage lock."""
        self.client.close()


def chunk_key(chunk: Chunk) -> str:
    """Unique key across bids: chunk ids come from file hashes, and the same
    file (e.g. a standard affidavit) can appear in several bids."""
    return f"{chunk.bid_id}/{chunk.chunk_id}"


def point_id(chunk: Chunk) -> str:
    """Stable UUID for a chunk (Qdrant ids must be UUIDs or integers)."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_key(chunk)))


def to_qdrant_filter(filters: SearchFilters | None) -> models.Filter | None:
    """Translate SearchFilters into a Qdrant filter (all conditions must hold)."""
    if filters is None:
        return None
    must: list[models.Condition] = []
    bids = filters.bid_ids or ([filters.bid_id] if filters.bid_id else None)
    if bids:
        must.append(models.FieldCondition(key="bid_id", match=models.MatchAny(any=bids)))
    if filters.doc_types:
        must.append(models.FieldCondition(
            key="doc_type", match=models.MatchAny(any=[t.value for t in filters.doc_types])))
    if filters.addendum_number is not None:
        must.append(models.FieldCondition(
            key="addendum_number", match=models.MatchValue(value=filters.addendum_number)))
    return models.Filter(must=must) if must else None
