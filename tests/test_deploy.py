"""Deployment switches: Qdrant server client, remote embeddings, settings overlay, re-ranker switches."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml

from rfp_intel import config
from rfp_intel.config import deep_merge, settings
from rfp_intel.schemas import SearchFilters
from rfp_intel.search import vector_store
from rfp_intel.search.embedder import RemoteEmbedder, make_embedder
from rfp_intel.search.engine import SearchEngine
from tests.search_fakes import FakeEmbedder, FakeReranker


# ---------------------------------------------------------------- vector store client


class _StubClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def collection_exists(self, name):
        return True


def test_make_client_local_and_server(tmp_path, monkeypatch):
    monkeypatch.setattr(vector_store, "QdrantClient", _StubClient)
    local = vector_store.make_client("qdrant_local", tmp_path / "q")
    assert local.kwargs == {"path": str(tmp_path / "q")} and (tmp_path / "q").is_dir()
    server = vector_store.make_client("qdrant_server", tmp_path / "unused", url="http://qdrant:6333", api_key="k")
    assert server.kwargs == {"url": "http://qdrant:6333", "api_key": "k"} and not (tmp_path / "unused").exists()
    assert vector_store.make_client("qdrant_server", tmp_path, url="http://q:6333").kwargs["api_key"] is None
    with pytest.raises(ValueError, match="QDRANT_URL"):
        vector_store.make_client("qdrant_server", tmp_path)
    with pytest.raises(ValueError, match="unknown vector_store.kind"):
        vector_store.make_client("pinecone", tmp_path)


def test_vector_store_uses_settings_for_the_server(tmp_path, monkeypatch):
    monkeypatch.setattr(vector_store, "QdrantClient", _StubClient)
    monkeypatch.setattr(settings.vector_store, "kind", "qdrant_server")
    monkeypatch.setattr(settings.env, "qdrant_url", "http://qdrant:6333")
    store = vector_store.VectorStore(tmp_path / "q", "rfp_chunks", 8)
    assert store.client.kwargs == {"url": "http://qdrant:6333", "api_key": None}


# ---------------------------------------------------------------- remote embedder


class _FakeEmbeddings:
    def __init__(self):
        self.calls: list[dict] = []

    def create(self, model, input):
        self.calls.append({"model": model, "input": list(input)})
        # reversed order on purpose: the embedder must sort by index
        data = [SimpleNamespace(index=i, embedding=[float(len(t)), 1.0, 0.0]) for i, t in enumerate(input)]
        return SimpleNamespace(data=list(reversed(data)))


def test_remote_embedder_batches_normalizes_and_prefixes_queries():
    fake = SimpleNamespace(embeddings=_FakeEmbeddings())
    emb = RemoteEmbedder(model_name="text-embedding-3-small", batch_size=2, query_prefix="Q: ", client=fake)
    vectors = emb.embed_documents(["aaaa", "bb", "c"])
    assert len(vectors) == 3 and len(fake.embeddings.calls) == 2           # 2 + 1
    assert fake.embeddings.calls[0] == {"model": "text-embedding-3-small", "input": ["aaaa", "bb"]}
    assert vectors[0][0] > vectors[1][0] > vectors[2][0]                    # order preserved despite reversed data
    assert all(abs(sum(v * v for v in vec) - 1.0) < 1e-9 for vec in vectors)
    assert emb.dim == 3
    emb.embed_queries(["due date"])
    assert fake.embeddings.calls[-1]["input"] == ["Q: due date"]
    assert emb.embed_query("x") == emb.embed_queries(["x"])[0]
    assert emb.embed_documents([]) == []


def test_make_embedder_follows_the_provider(monkeypatch):
    monkeypatch.setattr(settings.embedding, "provider", "openai")
    assert isinstance(make_embedder(), RemoteEmbedder)
    monkeypatch.setattr(settings.embedding, "provider", "local")
    assert type(make_embedder()).__name__ == "Embedder"
    monkeypatch.setattr(settings.embedding, "provider", "cohere")
    with pytest.raises(ValueError, match="unknown embedding.provider"):
        make_embedder()


def test_remote_embedder_needs_the_llm_key(monkeypatch):
    monkeypatch.setattr(settings.env, "llm_api_key", config.SecretStr(""))
    with pytest.raises(RuntimeError, match="LLM_API_KEY"):
        _ = RemoteEmbedder(client=None).client


# ---------------------------------------------------------------- settings overlay


def test_deep_merge_keeps_untouched_keys():
    base = {"a": {"x": 1, "y": 2}, "b": [1, 2], "c": 3}
    merged = deep_merge(base, {"a": {"y": 20, "z": 30}, "b": [9]})
    assert merged == {"a": {"x": 1, "y": 20, "z": 30}, "b": [9], "c": 3}
    assert base == {"a": {"x": 1, "y": 2}, "b": [1, 2], "c": 3}


def test_settings_overlay_file(tmp_path, monkeypatch):
    overlay = tmp_path / "docker.yaml"
    overlay.write_text(yaml.safe_dump({"vector_store": {"kind": "qdrant_server"}, "api": {"ask_workers": 4}}),
                       encoding="utf-8")
    monkeypatch.setenv("RFP_SETTINGS_OVERLAY", str(overlay))
    loaded = config.load_settings()
    assert loaded.vector_store.kind == "qdrant_server" and loaded.vector_store.collection == "rfp_chunks"
    assert loaded.api.ask_workers == 4 and loaded.api.cors_origins == settings.api.cors_origins
    assert loaded.env.settings_overlay == str(overlay)


def test_shipped_docker_overlay_is_valid():
    monkeypatch_free = yaml.safe_load((config.CONFIG_DIR / "docker.yaml").read_text(encoding="utf-8"))
    merged = deep_merge(config._read_yaml("settings.yaml"), monkeypatch_free)
    loaded = config.Settings(**merged)
    assert loaded.vector_store.kind == "qdrant_server" and loaded.api.ask_workers == 4


# ---------------------------------------------------------------- re-ranker switches


@pytest.fixture
def engine(tmp_path, mini_bid):
    eng = SearchEngine(storage_dir=tmp_path / "storage", embedder=FakeEmbedder(), reranker=FakeReranker())
    eng.index_bid(mini_bid)
    yield eng
    eng.close()


def test_reranker_can_be_disabled_or_capped(engine, monkeypatch):
    filters = SearchFilters(bid_id=engine.known_bids()[0])
    hits = engine.search("Laptop Quantity", filters, config="hybrid_rerank")
    assert hits and all("rerank" in h.ranks for h in hits) and engine.reranker.calls == 1

    monkeypatch.setattr(settings.reranker, "max_passages", 2)
    engine.search("Laptop Quantity", filters, config="hybrid_rerank", top_k=10)
    assert engine.reranker.calls == 2
    reranked = [h for h in engine.search("Laptop Quantity", filters, config="hybrid_rerank", top_k=10) if "rerank" in h.ranks]
    assert len(reranked) <= 2

    monkeypatch.setattr(settings.reranker, "enabled", False)
    calls_before = engine.reranker.calls
    hits = engine.search("Laptop Quantity", filters, config="hybrid_rerank")
    assert hits and not any("rerank" in h.ranks for h in hits) and engine.reranker.calls == calls_before
