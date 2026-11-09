"""Incremental indexing via storage/manifest.json: no duplicates, changes trigger rebuilds."""

import shutil

import pymupdf
import pytest

from rfp_intel.config import settings
from rfp_intel.search.engine import SearchEngine
from tests.search_fakes import FakeEmbedder, FakeReranker


@pytest.fixture
def folder(tmp_path, mini_bid):
    target = tmp_path / "data" / "MiniBid"
    shutil.copytree(mini_bid, target)
    return target


@pytest.fixture
def engine(tmp_path):
    eng = SearchEngine(storage_dir=tmp_path / "storage", embedder=FakeEmbedder(), reranker=FakeReranker())
    yield eng
    eng.close()


def test_second_run_adds_zero_chunks(engine, folder):
    first = engine.index_bid(folder)
    second = engine.index_bid(folder)
    assert first.chunks_added > 0 and not first.skipped
    assert second.skipped and second.chunks_added == 0
    assert second.chunks_total == first.chunks_total == engine.vectors.count("MiniBid")


def test_changed_file_rebuilds_without_duplicates(engine, folder):
    first = engine.index_bid(folder)
    doc = pymupdf.open()
    page = doc.new_page()
    for i in range(6):
        page.insert_text((50, 60 + 20 * i), f"Addendum line {i}: the delivery schedule for laptops changes to sixty days.")
    doc.save(folder / "Addendum_2.pdf")
    second = engine.index_bid(folder)
    assert not second.skipped
    assert second.chunks_total == second.chunks_added == engine.vectors.count("MiniBid")
    assert second.chunks_total > first.chunks_total


def test_force_reindexes(engine, folder):
    engine.index_bid(folder)
    again = engine.index_bid(folder, force=True)
    assert not again.skipped and again.chunks_added == again.chunks_total


def test_chunking_settings_change_triggers_reindex(engine, folder, monkeypatch):
    engine.index_bid(folder)
    monkeypatch.setattr(settings.chunking, "max_tokens", 300)
    assert not engine.index_bid(folder).skipped


def test_reindexing_one_bid_leaves_others(engine, folder, tmp_path):
    other = tmp_path / "data" / "Other"
    shutil.copytree(folder, other)
    engine.index_bid(folder)
    engine.index_bid(other)
    before = engine.vectors.count("Other")
    engine.index_bid(folder, force=True)
    assert engine.vectors.count("Other") == before
    assert engine.known_bids() == ["MiniBid", "Other"]


def test_manifest_survives_restart(tmp_path, folder):
    storage = tmp_path / "storage"
    first = SearchEngine(storage_dir=storage, embedder=FakeEmbedder(), reranker=FakeReranker())
    first.index_bid(folder)
    first.close()
    second = SearchEngine(storage_dir=storage, embedder=FakeEmbedder(), reranker=FakeReranker())
    try:
        assert second.index_bid(folder).skipped
    finally:
        second.close()
