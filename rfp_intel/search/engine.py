"""SearchEngine: the single entry point to indexing and hybrid search (BUILD_PLAN 6.2).

    engine = SearchEngine()
    engine.index_bid(Path("data/Bid1"))          # incremental via storage/manifest.json
    engine.search("due date", SearchFilters(bid_id="Bid1"), top_k=5, config="hybrid_rerank")

Search: parse filters from the query -> expand the query -> vector and BM25
retrieval per query variant -> RRF -> cross-encoder re-rank -> top_k hits.
`search_many` does the same for several queries about one topic: one fusion,
a per-query share of the re-rank pool and one re-rank call that scores every
candidate against every query (the Retrieval agent uses it for every evidence
request).
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Protocol

from rfp_intel.config import settings
from rfp_intel.ingestion.loader import SUPPORTED_SUFFIXES, load_bid_folder
from rfp_intel.schemas import Chunk, IndexStats, SearchFilters, SearchHit
from rfp_intel.search.answer_cache import AnswerCache, index_fingerprint
from rfp_intel.search.bm25_store import BM25Store
from rfp_intel.search.chunker import chunk_document, embed_text
from rfp_intel.search.embedder import make_embedder
from rfp_intel.search.fusion import rrf
from rfp_intel.search.query import expand_query, parse_filters
from rfp_intel.search.reranker import Reranker
from rfp_intel.search.vector_store import VectorStore, chunk_key

logger = logging.getLogger(__name__)
_QUERY_CACHE_SIZE = 512          # memoised query embeddings (query text -> vector)
_REMOVE_LOCK_TIMEOUT = 5.0       # seconds remove_bid waits for an index in progress before giving up
_EMBED_BATCH = 64                # texts per embed call, so a stop request is seen between batches


class IndexCancelled(Exception):
    """`index_bid` was stopped by its `should_stop` callback; no store was changed."""


class EmbedderLike(Protocol):
    """What the engine needs from an embedder (lets tests use a fake)."""

    @property
    def dim(self) -> int: ...
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_queries(self, texts: list[str]) -> list[list[float]]: ...


class RerankerLike(Protocol):
    """What the engine needs from a re-ranker."""

    def score(self, query: str, passages: list[str]) -> list[float]: ...


class SearchEngine:
    """Indexes bid folders and answers hybrid searches over them."""

    def __init__(self, storage_dir: Path | None = None, embedder: EmbedderLike | None = None,
                 reranker: RerankerLike | None = None) -> None:
        self.storage_dir = Path(storage_dir or settings.path("storage_dir"))
        self.embedder: EmbedderLike = embedder or make_embedder()
        self.reranker: RerankerLike = reranker or Reranker()
        self.bm25 = BM25Store(self.storage_dir / "bm25")
        self.manifest_path = self.storage_dir / "manifest.json"
        self._vectors: VectorStore | None = None
        self._query_cache: dict[str, list[float]] = {}
        self._answer_cache: AnswerCache | None = None
        # Serializes index_bid / remove_bid: both read the manifest, mutate the
        # stores and write it back, so two at once would lose one bid's entry.
        self._write_lock = threading.Lock()

    @property
    def vectors(self) -> VectorStore:
        """The Qdrant store, opened on first use.

        The vector size is remembered in the manifest so that skipping an
        unchanged bid does not load the embedding model just to open the store.
        """
        if self._vectors is None:
            manifest = self._read_manifest()
            dim = manifest.get("_dim") or self.embedder.dim
            self._vectors = VectorStore(self.storage_dir / "qdrant", settings.vector_store.collection, dim)
            if manifest.get("_dim") != dim:
                manifest["_dim"] = dim
                self._write_manifest(manifest)
        return self._vectors

    @property
    def answer_cache(self) -> AnswerCache:
        """The semantic answer cache, in the same Qdrant store as the chunks (opened on first use)."""
        if self._answer_cache is None:
            store = self.vectors                      # opens the client and fixes the dimension
            dim = self._read_manifest().get("_dim") or self.embedder.dim
            self._answer_cache = AnswerCache(store.client, self.embedder, dim)
        return self._answer_cache

    def index_fingerprint(self, bids: list[str]) -> str:
        """Hash of the index state of `bids` (every indexed bid when empty); changes on re-index."""
        return index_fingerprint(self._read_manifest(), bids)

    def warm_up(self) -> None:
        """Load the embedding and re-ranking models now (first use otherwise costs ~30 s)."""
        self.embedder.embed_queries(["warm up"])
        self.reranker.score("warm up", ["warm up"])

    def close(self) -> None:
        """Release the vector store's file lock."""
        if self._vectors is not None:
            self._vectors.close()
            self._vectors = None
        self._answer_cache = None

    # ---------------------------------------------------------------- indexing

    def known_bids(self) -> list[str]:
        """Bids present in the manifest."""
        return sorted(key for key in self._read_manifest() if not key.startswith("_"))

    def remove_bid(self, bid_id: str) -> bool:
        """Drop a bid's chunks from both stores and the manifest; False when it was not indexed.

        `index_bid` holds the write lock for a whole (possibly minutes-long)
        indexing run, so rather than hang a DELETE request behind it, give up
        after a short wait and raise RuntimeError ("busy, try again").
        """
        if not self._write_lock.acquire(timeout=_REMOVE_LOCK_TIMEOUT):
            raise RuntimeError("the index is busy (a bid is being indexed); try again when it finishes")
        try:
            manifest = self._read_manifest()
            if bid_id not in manifest:
                return False
            self.vectors.delete_by_bid(bid_id)
            self.bm25.delete(bid_id)
            del manifest[bid_id]
            self._write_manifest(manifest)
        finally:
            self._write_lock.release()
        logger.info("removed bid %s from the index", bid_id)
        return True

    def index_bid(self, folder: Path, force: bool = False,
                  should_stop: Callable[[], bool] | None = None) -> IndexStats:
        """Index a bid folder; unchanged bids are skipped, changed bids are rebuilt.

        Held under `_write_lock` for the whole operation: the manifest is read at
        the start and written at the end, so two concurrent indexes (two uploads,
        or an upload and a CLI run) would otherwise drop one bid's entry and leave
        it indexed but invisible to `known_bids`.

        `should_stop` is polled during the slow phases (parsing, embedding) and
        a True answer raises IndexCancelled. Every check happens before the
        stores are touched, so a cancelled index leaves them exactly as they were.
        """
        def checkpoint() -> None:
            if should_stop is not None and should_stop():
                raise IndexCancelled(f"indexing of {bid_id} was stopped on request")

        started = time.perf_counter()
        folder = Path(folder)
        bid_id = folder.name
        state = {"files": _file_hashes(folder), "config": _index_config_key()}
        with self._write_lock:
            manifest = self._read_manifest()
            if not force and manifest.get(bid_id) == state:
                return IndexStats(bid_id=bid_id, documents=len(state["files"]), chunks_added=0,
                                  chunks_total=self.vectors.count(bid_id), skipped=True,
                                  seconds=round(time.perf_counter() - started, 2))

            checkpoint()
            documents = load_bid_folder(folder)
            chunks = [chunk for doc in documents for chunk in chunk_document(doc)]
            texts = [embed_text(chunk) for chunk in chunks]
            vectors: list[list[float]] = []
            for i in range(0, len(texts), _EMBED_BATCH):
                checkpoint()
                vectors.extend(self.embedder.embed_documents(texts[i:i + _EMBED_BATCH]))
            checkpoint()
            self.vectors.delete_by_bid(bid_id)
            self.vectors.upsert(chunks, vectors)
            self.bm25.save(bid_id, chunks, texts)
            manifest[bid_id] = state
            self._write_manifest(manifest)
        return IndexStats(
            bid_id=bid_id,
            documents=len(documents),
            chunks_added=len(chunks),
            chunks_total=self.vectors.count(bid_id),
            warnings=[f"{d.file_name}: {w}" for d in documents for w in d.warnings],
            seconds=round(time.perf_counter() - started, 2),
        )

    # ---------------------------------------------------------------- search

    def search(self, query: str, filters: SearchFilters | None = None, top_k: int | None = None,
               config: str | None = None) -> list[SearchHit]:
        """Hybrid search. Explicit `filters` win over filters parsed from the query."""
        return self.search_many([query], filters, top_k=top_k, config=config)

    def search_many(self, queries: list[str], filters: SearchFilters | None = None,
                    top_k: int | None = None, config: str | None = None) -> list[SearchHit]:
        """Hybrid search for several queries about the same topic, fused and re-ranked once.

        Every query (and its synonym variants) contributes a vector list and a
        BM25 list to one RRF fusion. The re-rank pool is the top `rerank_top_n`
        fused candidates plus each query's own top share (rerank_top_n divided
        by the number of queries), so a passage that answers only one
        sub-topic of a multi-topic field is still a candidate. The cross-encoder
        scores the pool against every query in one batched call and keeps each
        candidate's best score. One query embedding batch, one re-rank call.
        """
        cfg = settings.retrieval_for(config)
        queries = [q for q in queries if q and q.strip()]
        if not queries:
            return []
        known = self.known_bids()
        variants: list[tuple[str, SearchFilters, str]] = []
        rerank_queries: list[str] = []
        for query in queries:
            parsed, cleaned = parse_filters(query, known)
            merged = merge_filters(parsed, filters)
            if cleaned and cleaned not in rerank_queries:
                rerank_queries.append(cleaned)
            variants.extend((variant, merged, cleaned) for variant in expand_query(cleaned))

        chunks: dict[str, Chunk] = {}
        ranks: dict[str, dict[str, int]] = {}
        per_query: dict[str, list[list[str]]] = {}      # cleaned query -> its ranked lists
        vectors = self._query_vectors([text for text, _, _ in variants])
        for (variant, merged, cleaned), vector in zip(variants, vectors, strict=True):
            lists = per_query.setdefault(cleaned, [])
            hits = self.vectors.search(vector, merged, cfg.candidates_per_retriever)
            lists.append(_collect(hits, "vector", chunks, ranks))
            if cfg.use_bm25:
                hits = self.bm25.search(variant, merged, cfg.candidates_per_retriever)
                lists.append(_collect(hits, "bm25", chunks, ranks))

        fused = rrf([ranked for lists in per_query.values() for ranked in lists], cfg.rrf_k)
        if cfg.use_reranker and settings.reranker.enabled and fused:
            top_n = min(cfg.rerank_top_n, settings.reranker.max_passages or cfg.rerank_top_n)
            pool = _candidate_pool(fused, per_query, top_n, cfg.rrf_k)
            fused = self._rerank(rerank_queries, pool, chunks, ranks)
        limit = top_k or cfg.final_top_k
        return [SearchHit(chunk=chunks[cid], score=score, ranks=ranks[cid]) for cid, score in fused[:limit]]

    def _query_vectors(self, texts: list[str]) -> list[list[float]]:
        """Query embeddings, memoised per query text; unseen texts are embedded in one batch."""
        missing = list(dict.fromkeys(t for t in texts if t not in self._query_cache))
        if missing:
            for text, vector in zip(missing, self.embedder.embed_queries(missing), strict=True):
                self._query_cache[text] = vector
            while len(self._query_cache) > _QUERY_CACHE_SIZE:
                self._query_cache.pop(next(iter(self._query_cache)))
        return [self._query_cache[t] for t in texts]

    def _rerank(self, queries: list[str], ids: list[str], chunks: dict[str, Chunk],
                ranks: dict[str, dict[str, int]]) -> list[tuple[str, float]]:
        """Re-order candidate ids by their best cross-encoder score over `queries` (one call)."""
        queries = queries or [""]
        passages = [embed_text(chunks[cid]) for cid in ids]
        scores = self.reranker.score_pairs([(query, passage) for query in queries for passage in passages])
        best = [max(scores[i::len(passages)]) for i in range(len(passages))]
        ordered = sorted(zip(ids, best), key=lambda pair: pair[1], reverse=True)
        for rank, (cid, _) in enumerate(ordered, start=1):
            ranks[cid]["rerank"] = rank
        return ordered

    # ---------------------------------------------------------------- manifest

    def _read_manifest(self) -> dict:
        """bid_id -> {"files": {name: sha1}, "config": key}."""
        if not self.manifest_path.exists():
            return {}
        try:
            return json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("manifest unreadable; all bids will be re-indexed")
            return {}

    def _write_manifest(self, manifest: dict) -> None:
        """Write the manifest atomically."""
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.manifest_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.manifest_path)


def merge_filters(parsed: SearchFilters, explicit: SearchFilters | None) -> SearchFilters:
    """Explicit filter fields override the ones parsed from the query text."""
    if explicit is None:
        return parsed
    data = parsed.model_dump()
    data.update(explicit.model_dump(exclude_none=True))
    if explicit.bid_id or explicit.bid_ids:
        data["bid_id"], data["bid_ids"] = explicit.bid_id, explicit.bid_ids
    return SearchFilters(**data)


def _candidate_pool(fused: list[tuple[str, float]], per_query: dict[str, list[list[str]]],
                    top_n: int, rrf_k: int) -> list[str]:
    """Re-rank candidates: the global fused top_n plus every query's own top share.

    With several queries the global fusion favours passages that match the
    first query's wording; reserving top_n / n_queries slots per query keeps
    a passage that is the best answer to one sub-topic in the pool.
    """
    pool = [cid for cid, _ in fused[:top_n]]
    if len(per_query) > 1:
        share = -(-top_n // len(per_query))              # ceiling division
        for lists in per_query.values():
            pool += [cid for cid, _ in rrf(lists, rrf_k)[:share] if cid not in pool]
    return pool


def _collect(hits: list[tuple[Chunk, float]], retriever: str, chunks: dict[str, Chunk],
             ranks: dict[str, dict[str, int]]) -> list[str]:
    """Record chunks and each chunk's best rank for a retriever; return the ranked ids."""
    ids: list[str] = []
    for rank, (chunk, _) in enumerate(hits, start=1):
        key = chunk_key(chunk)
        chunks.setdefault(key, chunk)
        best = ranks.setdefault(key, {})
        best[retriever] = min(best.get(retriever, rank), rank)
        ids.append(key)
    return ids


def _file_hashes(folder: Path) -> dict[str, str]:
    """sha1 of every supported file in a folder."""
    return {
        p.name: hashlib.sha1(p.read_bytes()).hexdigest()
        for p in sorted(folder.iterdir())
        if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
    }


def _index_config_key() -> str:
    """Hash of the settings that change chunks or vectors (re-index when they change)."""
    relevant = {
        "chunking": settings.chunking.model_dump(),
        "embedding_model": settings.embedding.model,
        "collection": settings.vector_store.collection,
    }
    return hashlib.sha1(json.dumps(relevant, sort_keys=True).encode()).hexdigest()[:12]
