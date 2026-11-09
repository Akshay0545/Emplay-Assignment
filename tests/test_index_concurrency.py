"""Concurrent indexing must not lose a bid's manifest entry.

`index_bid` reads the manifest, writes both stores and writes the manifest back.
Two of those running at once (two uploads, or an upload while the CLI indexes)
used to drop one bid: its chunks were searchable but `known_bids` never listed
it, so it showed as "not indexed" and could never be skipped incrementally.
"""

from __future__ import annotations

import shutil
from concurrent.futures import ThreadPoolExecutor

import pytest

from rfp_intel.search.engine import SearchEngine
from tests.search_fakes import FakeEmbedder, FakeReranker

BIDS = ["AlphaBid", "BetaBid", "GammaBid", "DeltaBid"]


@pytest.fixture
def engine(tmp_path):
    eng = SearchEngine(storage_dir=tmp_path / "storage", embedder=FakeEmbedder(), reranker=FakeReranker())
    yield eng
    eng.close()


@pytest.fixture
def folders(tmp_path, mini_bid):
    """One copy of the mini bid per name, so each indexes as its own bid."""
    made = []
    for name in BIDS:
        target = tmp_path / "data" / name
        shutil.copytree(mini_bid, target)
        made.append(target)
    return made


def test_concurrent_index_keeps_every_bid_in_the_manifest(engine, folders):
    with ThreadPoolExecutor(max_workers=len(folders)) as pool:
        stats = list(pool.map(engine.index_bid, folders))

    assert all(s.chunks_added > 0 and not s.skipped for s in stats)
    assert engine.known_bids() == sorted(BIDS), "a concurrent index dropped a manifest entry"
    for name in BIDS:
        assert engine.vectors.count(name) > 0
    assert sorted(engine.bm25.bids()) == sorted(BIDS)


def test_a_bid_indexed_concurrently_is_still_skipped_on_the_next_run(engine, folders):
    """The manifest entry must survive, otherwise the bid is re-indexed every time."""
    with ThreadPoolExecutor(max_workers=len(folders)) as pool:
        list(pool.map(engine.index_bid, folders))

    again = [engine.index_bid(f) for f in folders]
    assert all(s.skipped and s.chunks_added == 0 for s in again)


def test_concurrent_index_and_remove_leave_a_consistent_manifest(engine, folders):
    for folder in folders:
        engine.index_bid(folder)

    keep, drop = folders[:2], folders[2:]
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(engine.remove_bid, f.name) for f in drop]
        futures += [pool.submit(engine.index_bid, f) for f in keep]
        [f.result() for f in futures]

    assert engine.known_bids() == sorted(f.name for f in keep)
    for folder in drop:
        assert engine.vectors.count(folder.name) == 0
