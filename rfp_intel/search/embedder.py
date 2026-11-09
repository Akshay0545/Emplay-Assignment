"""Embedders: local sentence-transformers (default) or the OpenAI embeddings API.

Both return normalized vectors and add `embedding.query_prefix` to queries
only. `make_embedder()` picks one from `embedding.provider`.
"""

from __future__ import annotations

from functools import cached_property
from typing import Any, Callable

from rfp_intel.config import settings

_torch_configured = False


def configure_torch() -> None:
    """Apply `embedding.torch_threads` once per process (before a local model is loaded)."""
    global _torch_configured
    threads = settings.embedding.torch_threads
    if _torch_configured or threads <= 0:
        return
    import torch

    torch.set_num_threads(threads)
    _torch_configured = True


def load_cached(factory: Callable[..., Any], model_name: str) -> Any:
    """Load a Hugging Face model from the local cache; download only if it is missing."""
    configure_torch()
    try:
        return factory(model_name, local_files_only=True)
    except Exception:  # noqa: BLE001 - not cached yet (first run): download it
        return factory(model_name)


def make_embedder() -> "Embedder | RemoteEmbedder":
    """The embedder selected by `embedding.provider` (local | openai)."""
    provider = settings.embedding.provider
    if provider == "local":
        return Embedder()
    if provider == "openai":
        return RemoteEmbedder()
    raise ValueError(f"unknown embedding.provider {provider!r} (use local or openai)")


class Embedder:
    """Embeds chunk texts and queries with a local model (loaded on first use)."""

    def __init__(self, model_name: str | None = None, batch_size: int | None = None,
                 query_prefix: str | None = None) -> None:
        cfg = settings.embedding
        self.model_name = model_name or cfg.model
        self.batch_size = batch_size or cfg.batch_size
        self.query_prefix = cfg.query_prefix if query_prefix is None else query_prefix

    @cached_property
    def _model(self):  # noqa: ANN202 - SentenceTransformer, imported lazily (slow import)
        """The sentence-transformers model, from the local cache when available."""
        from sentence_transformers import SentenceTransformer

        return load_cached(SentenceTransformer, self.model_name)

    @property
    def dim(self) -> int:
        """Embedding dimension."""
        model = self._model
        getter = getattr(model, "get_embedding_dimension", None) or model.get_sentence_embedding_dimension
        return int(getter())

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Normalized embeddings for chunk texts (no prefix)."""
        if not texts:
            return []
        vectors = self._model.encode(texts, batch_size=self.batch_size,
                                     normalize_embeddings=True, show_progress_bar=False)
        return [v.tolist() for v in vectors]

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        """Normalized embeddings for several queries in one model call (retrieval prefix added)."""
        if not texts:
            return []
        vectors = self._model.encode([self.query_prefix + t for t in texts], batch_size=self.batch_size,
                                     normalize_embeddings=True, show_progress_bar=False)
        return [v.tolist() for v in vectors]

    def embed_query(self, text: str) -> list[float]:
        """Normalized embedding for a query, with the retrieval prefix."""
        return self.embed_queries([text])[0]


class RemoteEmbedder:
    """OpenAI embeddings API (`embedding.model`, e.g. text-embedding-3-small) with the LLM key.

    Keeps the embedding work off the API host's CPU. Texts are sent in
    batches of `embedding.batch_size`; vectors come back normalized.
    """

    def __init__(self, model_name: str | None = None, batch_size: int | None = None,
                 query_prefix: str | None = None, client: Any | None = None) -> None:
        cfg = settings.embedding
        self.model_name = model_name or cfg.model
        self.batch_size = batch_size or cfg.batch_size
        self.query_prefix = cfg.query_prefix if query_prefix is None else query_prefix
        self._client = client
        self._dim: int | None = None

    @property
    def client(self) -> Any:
        """The OpenAI client (created on first use; needs LLM_API_KEY)."""
        if self._client is None:
            import openai

            key = settings.env.llm_api_key.get_secret_value()
            if not key:
                raise RuntimeError("embedding.provider is openai but LLM_API_KEY is not set")
            self._client = openai.OpenAI(api_key=key, timeout=settings.llm.timeout_s, max_retries=2)
        return self._client

    @property
    def dim(self) -> int:
        """Embedding dimension (learned from the first vector)."""
        if self._dim is None:
            self._dim = len(self._embed(["dimension probe"])[0])
        return self._dim

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Normalized embeddings for chunk texts (no prefix)."""
        return self._embed(texts)

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        """Normalized embeddings for queries (prefix added)."""
        return self._embed([self.query_prefix + t for t in texts])

    def embed_query(self, text: str) -> list[float]:
        """Normalized embedding for one query."""
        return self.embed_queries([text])[0]

    def _embed(self, texts: list[str]) -> list[list[float]]:
        """Call the embeddings API in batches and normalize every vector."""
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = [t if t.strip() else " " for t in texts[start:start + self.batch_size]]
            response = self.client.embeddings.create(model=self.model_name, input=batch)
            vectors.extend(_normalize(list(item.embedding)) for item in sorted(response.data, key=lambda d: d.index))
        if self._dim is None and vectors:
            self._dim = len(vectors[0])
        return vectors


def _normalize(vector: list[float]) -> list[float]:
    """Unit-length copy of `vector` (unchanged when it is all zeros)."""
    norm = sum(v * v for v in vector) ** 0.5
    return [v / norm for v in vector] if norm else vector
