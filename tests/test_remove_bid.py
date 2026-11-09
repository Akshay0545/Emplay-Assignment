"""Removing a bid: search engine stores, service clean-up, DELETE /bids/{id} and the CLI."""

from __future__ import annotations

import shutil
import threading
import time

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

import main
from rfp_intel import services
from rfp_intel.agents import runtime
from rfp_intel.api.server import app
from rfp_intel.config import settings
from rfp_intel.logging_utils import clear_cancel, is_cancel_requested
from rfp_intel.search.engine import IndexCancelled, SearchEngine
from tests.graph_fakes import FakeEngine, ScriptedLLM, install
from tests.search_fakes import FakeEmbedder, FakeReranker

BID = "BidT"


# ---------------------------------------------------------------- engine (real stores in tmp)

def test_engine_remove_bid_clears_both_stores_and_the_manifest(tmp_path, mini_bid):
    folder = tmp_path / "data" / "MiniBid"
    shutil.copytree(mini_bid, folder)
    engine = SearchEngine(storage_dir=tmp_path / "storage", embedder=FakeEmbedder(), reranker=FakeReranker())
    try:
        stats = engine.index_bid(folder)
        assert stats.chunks_total > 0 and engine.known_bids() == ["MiniBid"]
        assert engine.remove_bid("MiniBid") is True
        assert engine.known_bids() == [] and engine.vectors.count("MiniBid") == 0
        assert engine.bm25.bids() == []
        assert engine.remove_bid("MiniBid") is False          # already gone: no error
        again = engine.index_bid(folder)                      # can be indexed afresh
        assert not again.skipped and again.chunks_added == stats.chunks_total
    finally:
        engine.close()


# ---------------------------------------------------------------- service + API + CLI (fake engine)

@pytest.fixture
def workspace(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / BID).mkdir(parents=True)
    (data / BID / "rfp.pdf").write_bytes(b"%PDF-1.4 fake")
    outputs = tmp_path / "outputs"
    (outputs / "parsed" / BID).mkdir(parents=True)
    (outputs / "parsed" / BID / "rfp.pdf.json").write_text("{}", encoding="utf-8")
    for name in (f"{BID}.json", f"{BID}_summary.md", f"{BID}_go_no_go.json", f"{BID}_go_no_go.md",
                 "Other.json", f"{BID}2.json"):
        (outputs / name).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(settings.paths, "data_dir", str(data))
    monkeypatch.setattr(settings.paths, "outputs_dir", str(outputs))
    monkeypatch.setattr(settings.paths, "logs_dir", str(tmp_path / "logs"))
    runtime.set_engine(FakeEngine(bid=BID))
    monkeypatch.setattr(runtime, "close_engine", lambda: None)
    install(monkeypatch, ScriptedLLM())
    services._runs.clear()
    yield tmp_path
    runtime.set_engine(None)


def test_service_removes_index_outputs_and_folder_only_for_that_bid(workspace):
    result = services.remove_bid(BID)
    assert result.indexed and result.folder_removed and result.outputs_removed == 5
    outputs = workspace / "outputs"
    assert not (workspace / "data" / BID).exists()
    assert not (outputs / f"{BID}.json").exists() and not (outputs / "parsed" / BID).exists()
    assert (outputs / "Other.json").exists() and (outputs / f"{BID}2.json").exists()   # other bids untouched


def test_service_errors(workspace, monkeypatch):
    with pytest.raises(ValueError):
        services.remove_bid("../etc")
    with pytest.raises(FileNotFoundError):
        services.remove_bid("Nope")
    # A run that never honours its cancel: remove gives up after the stop wait.
    monkeypatch.setattr(services, "_STOP_WAIT_SECONDS", 0.3)
    services._register_run("run-1", "extract", BID)
    try:
        with pytest.raises(RuntimeError, match="stopped|stopping"):
            services.remove_bid(BID)
        assert is_cancel_requested("run-1"), "the stop request stands, so a retry can succeed"
    finally:
        clear_cancel("run-1")


def test_remove_stops_a_running_extraction_then_deletes(workspace):
    """Clicking Remove during an extraction cancels it and deletes once it unwound."""
    services._register_run("run-1", "extract", BID)

    def stopper() -> None:  # the worker thread honouring the cancel flag
        while not is_cancel_requested("run-1"):
            time.sleep(0.01)
        services._set_run("run-1", "cancelled", "stopped on request")

    thread = threading.Thread(target=stopper)
    thread.start()
    try:
        result = services.remove_bid(BID)
    finally:
        thread.join(timeout=5)
        clear_cancel("run-1")
    assert result.folder_removed and not (workspace / "data" / BID).exists()


def test_remove_stops_an_upload_still_being_indexed(workspace):
    """Clicking Remove while the upload indexes cancels the indexing run first."""
    services._register_run("run-2", "index", BID)
    with services._runs_lock:
        services._indexing[BID] = "run-2"

    def stopper() -> None:  # the index worker noticing should_stop
        while not is_cancel_requested("run-2"):
            time.sleep(0.01)
        services._set_run("run-2", "cancelled", "stopped on request")
        with services._runs_lock:
            services._indexing.pop(BID, None)

    thread = threading.Thread(target=stopper)
    thread.start()
    try:
        result = services.remove_bid(BID)
    finally:
        thread.join(timeout=5)
        clear_cancel("run-2")
        with services._runs_lock:
            services._indexing.pop(BID, None)
    assert result.folder_removed and not (workspace / "data" / BID).exists()


def test_engine_index_bid_stops_before_touching_the_stores(tmp_path, mini_bid):
    """A cancelled index leaves the stores and manifest exactly as they were."""
    folder = tmp_path / "data" / "MiniBid"
    shutil.copytree(mini_bid, folder)
    engine = SearchEngine(storage_dir=tmp_path / "storage", embedder=FakeEmbedder(), reranker=FakeReranker())
    try:
        with pytest.raises(IndexCancelled):
            engine.index_bid(folder, should_stop=lambda: True)
        assert engine.known_bids() == []
        stats = engine.index_bid(folder)      # a later index starts clean
        assert not stats.skipped and stats.chunks_total > 0
    finally:
        engine.close()


def test_engine_remove_bid_refuses_instead_of_waiting_behind_an_index(tmp_path, monkeypatch):
    """A remove during a long index answers 'busy' quickly instead of hanging the request."""
    from rfp_intel.search import engine as engine_mod

    monkeypatch.setattr(engine_mod, "_REMOVE_LOCK_TIMEOUT", 0.05)
    engine = SearchEngine(storage_dir=tmp_path / "storage", embedder=FakeEmbedder(), reranker=FakeReranker())
    try:
        with engine._write_lock:  # what index_bid holds for a whole indexing run
            with pytest.raises(RuntimeError, match="busy"):
                engine.remove_bid("Whatever")
    finally:
        engine.close()


def test_locked_file_is_retried_then_reported_as_in_use(workspace, monkeypatch):
    """A transient Windows file lock is retried; a persistent one becomes a clear RuntimeError."""
    monkeypatch.setattr(services, "_LOCK_RETRY_SECONDS", 0.2)
    monkeypatch.setattr(services, "_LOCK_RETRY_INTERVAL", 0.01)
    calls = {"n": 0}

    def flaky() -> None:
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(32, "The process cannot access the file")

    services._delete_retrying(flaky, workspace / "data" / BID)
    assert calls["n"] == 3

    def stuck() -> None:
        raise PermissionError(32, "The process cannot access the file")

    with pytest.raises(RuntimeError, match="in use"):
        services._delete_retrying(stuck, workspace / "data" / BID)


def test_delete_endpoint_answers_409_when_a_file_stays_locked(workspace, monkeypatch):
    monkeypatch.setattr(services, "_LOCK_RETRY_SECONDS", 0.1)
    monkeypatch.setattr(services, "_LOCK_RETRY_INTERVAL", 0.01)

    def locked_rmtree(path, *args, **kwargs):
        raise PermissionError(32, "The process cannot access the file")

    monkeypatch.setattr(services.shutil, "rmtree", locked_rmtree)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.delete(f"/bids/{BID}")
    assert response.status_code == 409
    assert "in use" in response.json()["error"]


def test_delete_endpoint(workspace):
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.delete("/bids/bad%20id").status_code == 400
        assert client.delete("/bids/Nope").status_code == 404
        response = client.delete(f"/bids/{BID}")
        assert response.status_code == 200
        assert response.json() == {"bid_id": BID, "indexed": True, "outputs_removed": 5, "folder_removed": True}
        assert client.delete(f"/bids/{BID}").status_code == 404


def test_cli_remove(workspace):
    runner = CliRunner(env={"COLUMNS": "200"})
    result = runner.invoke(main.app, ["remove", BID])
    assert result.exit_code == 0, result.output
    assert "removed" in result.output and "outputs_removed=5" in result.output
    assert runner.invoke(main.app, ["remove", BID]).exit_code == 2
