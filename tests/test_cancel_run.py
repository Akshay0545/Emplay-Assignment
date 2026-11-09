"""Stopping a run mid-flight.

A run is a daemon thread and cannot be killed, so cancellation is cooperative:
`log_step` checks the flag before each node and raises `RunCancelled`, which
unwinds the graph. The node already in flight finishes first.
"""

from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

from rfp_intel import logging_utils, services
from rfp_intel.agents import runtime
from rfp_intel.api.server import app
from rfp_intel.config import settings
from rfp_intel.logging_utils import RunCancelled, clear_cancel, is_cancel_requested, log_step, request_cancel
from tests.graph_fakes import FakeEngine, ScriptedLLM, install

BID = "BidT"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / BID).mkdir(parents=True)
    (data / BID / "rfp.pdf").write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(settings.paths, "data_dir", str(data))
    monkeypatch.setattr(settings.paths, "outputs_dir", str(tmp_path / "outputs"))
    monkeypatch.setattr(settings.paths, "logs_dir", str(tmp_path / "logs"))
    monkeypatch.setattr(settings.env, "extract_model", "fake-extract")
    monkeypatch.setattr(settings.env, "reason_model", "fake-reason")
    runtime.set_engine(FakeEngine(bid=BID))
    install(monkeypatch, ScriptedLLM())
    services._runs.clear()
    yield tmp_path
    runtime.set_engine(None)
    services._runs.clear()


@pytest.fixture
def client(workspace):
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


# ---------------------------------------------------------------- the flag itself

def test_log_step_raises_once_the_run_is_cancelled():
    calls: list[str] = []

    @log_step(agent="Test", node="noop")
    def node(state: dict) -> dict:
        calls.append(state["run_id"])
        return {}

    state = {"run_id": "r-1"}
    node(state)
    assert calls == ["r-1"], "runs normally before any cancel"

    request_cancel("r-1")
    try:
        with pytest.raises(RunCancelled):
            node(state)
        assert calls == ["r-1"], "the node body is never entered after a cancel"
        node({"run_id": "r-2"})
        assert calls == ["r-1", "r-2"], "other runs are unaffected"
    finally:
        clear_cancel("r-1")


def test_a_node_error_is_still_swallowed_not_turned_into_a_cancel():
    """Only cancellation escapes log_step; a failing node still just logs."""
    @log_step(agent="Test", node="boom")
    def node(_state: dict) -> dict:
        raise ValueError("kaboom")

    result = node({"run_id": "r-err"})
    assert "boom" in result["errors"][0] and "kaboom" in result["errors"][0]


def test_cancel_raised_inside_a_node_escapes_log_step():
    """The retrieval node raises mid-body; that must unwind, not become an error entry."""
    @log_step(agent="Test", node="inner")
    def node(state: dict) -> dict:
        raise RunCancelled(state["run_id"])

    with pytest.raises(RunCancelled):
        node({"run_id": "r-inner"})

    # The per-node recorder must be reset on the cancel path too, or the next
    # tool call outside a node would write into a stale recorder.
    logging_utils.record_tool_call("search", query="after")


def test_retrieve_stops_between_fields(monkeypatch):
    """Retrieval is the slowest node, so it checks between fields, not only between nodes."""
    from types import SimpleNamespace

    from rfp_intel.agents.nodes import retrieve as retrieve_mod

    requests = [SimpleNamespace(field_or_question=name) for name in ("f1", "f2", "f3")]
    done: list[str] = []

    def fake_run_request(request):
        done.append(request.field_or_question)
        request_cancel("r-mid")          # the stop arrives while the first field runs
        return {}

    monkeypatch.setattr(retrieve_mod, "build_requests", lambda *a, **k: requests)
    monkeypatch.setattr(retrieve_mod, "run_request", fake_run_request)

    try:
        with pytest.raises(RunCancelled):
            retrieve_mod.retrieve.__wrapped__(
                {"run_id": "r-mid", "task": SimpleNamespace(group="dates_logistics")})
        assert done == ["f1"], "stopped after the field in flight, not after all three"
    finally:
        clear_cancel("r-mid")


def test_raise_if_cancelled_fires_inside_the_running_node():
    """A stop that arrives while a node runs is seen at the next checkpoint, not the next node."""
    @log_step(agent="Test", node="llm-ish")
    def node(state: dict) -> dict:
        request_cancel(state["run_id"])          # the stop arrives mid-node
        logging_utils.raise_if_cancelled()       # what llm._call does before each request
        raise AssertionError("not reached")

    try:
        with pytest.raises(RunCancelled):
            node({"run_id": "r-mid-node"})
    finally:
        clear_cancel("r-mid-node")


def test_llm_call_checks_the_flag_before_any_provider_request():
    """llm._call must not start another provider request once the run is cancelled."""
    from rfp_intel import llm as llm_mod

    @log_step(agent="Test", node="caller")
    def node(state: dict) -> dict:
        request_cancel(state["run_id"])
        llm_mod.text("hello", "any-model")       # would need an API key if it got further
        raise AssertionError("not reached")

    try:
        with pytest.raises(RunCancelled):
            node({"run_id": "r-llm"})
    finally:
        clear_cancel("r-llm")


def test_a_cancelled_llm_call_unwinds_the_node_instead_of_a_fallback(monkeypatch):
    """Nodes catch Exception around LLM calls, but RunCancelled must pass through."""
    from rfp_intel.agents.nodes import orchestrate as orch

    def cancelled(*_args, **_kwargs):
        raise RunCancelled("r-orch")

    monkeypatch.setattr(orch, "question_plan", cancelled)
    with pytest.raises(RunCancelled):
        orch.orchestrate.__wrapped__({"mode": "qa", "question": "x", "bid_ids": [], "run_id": "r-orch"})


def test_clear_cancel_is_idempotent():
    request_cancel("r-x")
    assert is_cancel_requested("r-x")
    clear_cancel("r-x")
    clear_cancel("r-x")
    assert not is_cancel_requested("r-x")


# ---------------------------------------------------------------- through the API

def test_cancel_stops_a_running_extraction_and_reports_it(client, monkeypatch):
    """The run stops at the next node boundary and ends as `cancelled`, not `failed`."""
    first_node = threading.Event()
    may_continue = threading.Event()
    original = services.extract

    def slow_extract(bid_id, run_id=None):
        first_node.set()
        assert may_continue.wait(timeout=30)
        return original(bid_id, run_id=run_id)

    monkeypatch.setattr(services, "extract", slow_extract)

    run_id = client.post("/extract", json={"bid_id": BID}).json()["run_id"]
    assert first_node.wait(timeout=10)

    cancelled = client.post(f"/runs/{run_id}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["run_id"] == run_id
    assert is_cancel_requested(run_id)

    may_continue.set()
    body = _wait_for(client, run_id)
    assert body["status"] == "cancelled" and "stopped on request" in body["error"]
    assert not is_cancel_requested(run_id), "the flag is cleared when the run ends"


def test_cancelling_releases_the_extraction_lock(client, monkeypatch):
    """A stopped run must not leave the single-extraction lock held."""
    may_continue = threading.Event()
    started = threading.Event()
    original = services.extract

    def slow_extract(bid_id, run_id=None):
        started.set()
        assert may_continue.wait(timeout=30)
        return original(bid_id, run_id=run_id)

    monkeypatch.setattr(services, "extract", slow_extract)
    run_id = client.post("/extract", json={"bid_id": BID}).json()["run_id"]
    assert started.wait(timeout=10)
    client.post(f"/runs/{run_id}/cancel")
    may_continue.set()
    _wait_for(client, run_id)

    second = client.post("/extract", json={"bid_id": BID})
    assert second.status_code == 202, "a new extraction can start after a cancelled one"
    _wait_for(client, second.json()["run_id"])


def test_cancel_errors(client):
    assert client.post("/runs/does-not-exist/cancel").status_code == 404

    run_id = client.post("/extract", json={"bid_id": BID}).json()["run_id"]
    _wait_for(client, run_id)
    already = client.post(f"/runs/{run_id}/cancel")
    assert already.status_code == 409 and "already" in already.json()["error"]


def _wait_for(client: TestClient, run_id: str, timeout: float = 60.0) -> dict:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/runs/{run_id}").json()
        if body["status"] != "running":
            return body
        time.sleep(0.1)
    raise AssertionError(f"run {run_id} still running after {timeout}s")
