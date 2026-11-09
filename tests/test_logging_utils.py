"""Tests for the StepLog trace writer and @log_step."""

import pytest

from rfp_intel import logging_utils
from rfp_intel.config import settings
from rfp_intel.logging_utils import log_step, read_trace, record_llm_call, record_tool_call
from rfp_intel.schemas import Usage


@pytest.fixture(autouse=True)
def tmp_logs(monkeypatch, tmp_path):
    monkeypatch.setattr(settings.paths, "logs_dir", str(tmp_path))
    return tmp_path


def test_log_step_writes_trace_and_appends_usage():
    @log_step(agent="Retrieval", node="retrieve")
    def node(state):
        record_tool_call("search", query="due date", hits=3)
        record_llm_call("m", Usage(tokens_in=10, tokens_out=4, cost_usd=0.01), latency_ms=7)
        return {"evidence": {"Due Date": "bundle"}}

    out = node({"run_id": "r1", "bid_ids": ["BidX"]})
    assert out["evidence"] == {"Due Date": "bundle"}
    [log] = out["usage"]
    assert (log.agent, log.node, log.step, log.error) == ("Retrieval", "retrieve", 1, None)
    assert (log.tokens_in, log.tokens_out, log.cost_usd) == (10, 4, 0.01)
    assert [call["kind"] for call in log.tool_calls] == ["search", "llm"]
    assert "bid_ids=list[1]" in log.input_summary
    assert read_trace("r1") == [log]


def test_log_step_catches_exceptions():
    @log_step(agent="Extraction")
    def extract_dates(state):
        raise TimeoutError("LLM timed out")

    out = extract_dates({"run_id": "r2"})
    assert out["errors"] == ["extract_dates: TimeoutError: LLM timed out"]
    assert out["usage"][0].error == "TimeoutError: LLM timed out"
    assert len(read_trace("r2")) == 1


def test_step_numbers_increase_within_a_run():
    @log_step(agent="A", node="n")
    def node(state):
        return None

    steps = [node({"run_id": "r3"})["usage"][0].step for _ in range(3)]
    assert steps == [1, 2, 3]


def test_recording_outside_a_step_is_a_no_op():
    record_tool_call("search", query="x")
    record_llm_call("m", Usage(), 1)


def test_new_run_id_is_unique():
    assert logging_utils.new_run_id() != logging_utils.new_run_id()
