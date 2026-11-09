"""Semantic answer cache on an embedded Qdrant store with the fake embedder."""

from __future__ import annotations

from qdrant_client import QdrantClient

from rfp_intel.config import settings
from rfp_intel.schemas import Answer
from rfp_intel.search.answer_cache import AnswerCache, index_fingerprint
from tests.search_fakes import FAKE_DIM, FakeEmbedder


def _answer(text: str) -> Answer:
    return Answer(question="original wording", answer=text, citations=[], confidence=0.9, bids=["Bid1"])


def test_hit_miss_scope_and_fingerprint(tmp_path):
    client = QdrantClient(path=str(tmp_path / "q"))
    cache = AnswerCache(client, FakeEmbedder(), FAKE_DIM, collection="answers_test")
    try:
        assert cache.get("When is the bid due?", ["Bid1"], "fp1") is None
        cache.put("When is the bid due?", ["Bid1"], "fp1", _answer("July 9, 2024"))
        assert cache.count() == 1

        hit = cache.get("when is the bid due", ["Bid1"], "fp1")          # same words, different case
        assert hit is not None and hit.cached is True and hit.answer == "July 9, 2024"
        assert hit.question == "when is the bid due"                       # the caller's wording is kept

        assert cache.get("When is the bid due?", ["Bid2"], "fp1") is None      # other scope
        assert cache.get("When is the bid due?", [], "fp1") is None           # all-bids scope differs
        assert cache.get("When is the bid due?", ["Bid1"], "fp2") is None     # bid re-indexed
        assert cache.get("What is the warranty period?", ["Bid1"], "fp1") is None   # unrelated question

        cache.put("Is a bond required?", ["Bid1"], "fp1", _answer("Not found in documents"))
        assert cache.count() == 1                                              # not-found is never cached

        cache.put("When is the bid due?", ["Bid1"], "fp1", _answer("July 10, 2024"))
        assert cache.count() == 1 and cache.get("When is the bid due?", ["Bid1"], "fp1").answer == "July 10, 2024"
        assert cache.clear() == 1 and cache.count() == 0
    finally:
        client.close()


def test_threshold_is_applied(tmp_path, monkeypatch):
    client = QdrantClient(path=str(tmp_path / "q"))
    cache = AnswerCache(client, FakeEmbedder(), FAKE_DIM, collection="answers_test")
    try:
        cache.put("bid due date deadline", ["Bid1"], "fp", _answer("July 9"))
        monkeypatch.setattr(settings.cache, "similarity_threshold", 0.5)
        assert cache.get("bid due date", ["Bid1"], "fp") is not None          # 3 of 4 tokens shared
        monkeypatch.setattr(settings.cache, "similarity_threshold", 0.99)
        assert cache.get("bid due date", ["Bid1"], "fp") is None
    finally:
        client.close()


def test_index_fingerprint_follows_the_bids_index_state():
    manifest = {"_dim": 384, "Bid1": {"files": {"a.pdf": "h1"}, "config": "c"}, "Bid2": {"files": {}, "config": "c"}}
    fp_all = index_fingerprint(manifest, [])
    assert fp_all == index_fingerprint(manifest, ["Bid1", "Bid2"]) == index_fingerprint(manifest, ["Bid2", "Bid1"])
    assert index_fingerprint(manifest, ["Bid1"]) != fp_all
    changed = {**manifest, "Bid1": {"files": {"a.pdf": "h2"}, "config": "c"}}
    assert index_fingerprint(changed, ["Bid1"]) != index_fingerprint(manifest, ["Bid1"])
    assert index_fingerprint(changed, ["Bid2"]) == index_fingerprint(manifest, ["Bid2"])
