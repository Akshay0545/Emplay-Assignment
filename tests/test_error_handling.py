"""LLM timeout, bad JSON, empty retrieval and a wrong API key never crash a run."""

import pytest

from rfp_intel import llm
from rfp_intel.agents import runtime
from rfp_intel.agents.graph import run_extraction, run_question
from rfp_intel.config import field_names, settings
from rfp_intel.logging_utils import read_trace
from tests.graph_fakes import FakeEngine, ScriptedLLM, install


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    (tmp_path / "data" / "BidT").mkdir(parents=True)
    monkeypatch.setattr(settings.paths, "data_dir", str(tmp_path / "data"))
    monkeypatch.setattr(settings.paths, "outputs_dir", str(tmp_path / "outputs"))
    monkeypatch.setattr(settings.paths, "logs_dir", str(tmp_path / "logs"))
    monkeypatch.setattr(settings.env, "extract_model", "fake-extract")
    monkeypatch.setattr(settings.env, "reason_model", "fake-reason")
    yield tmp_path
    runtime.set_engine(None)


@pytest.mark.parametrize("error", [
    llm.LLMError("OpenAI timeout after 60s"),
    llm.LLMError("invalid structured output for FieldExtractions: 2 validation errors"),
    llm.LLMError("OpenAI API error: AuthenticationError: Incorrect API key provided"),
])
def test_llm_failures_finish_with_failed_fields(workspace, monkeypatch, error):
    runtime.set_engine(FakeEngine())
    install(monkeypatch, ScriptedLLM(fail=error))
    state = run_extraction("BidT", run_id="t-fail")
    record = state["record"]
    assert set(record.fields) == set(field_names())
    statuses = {v.status for n, v in record.fields.items() if n != "Bid Summary"}
    assert statuses <= {"failed", "not_found"} and "failed" in statuses
    assert record.fields["Bid Summary"].status in {"failed", "not_found"}
    assert all(v.value is None for v in record.fields.values())
    assert record.validation["failed"] >= 1
    assert read_trace("t-fail")[-1].node == "report"
    assert (workspace / "outputs" / "BidT.json").exists()


def test_empty_retrieval_gives_not_found_without_llm_calls(workspace, monkeypatch):
    runtime.set_engine(FakeEngine(empty=True))
    scripted = ScriptedLLM()
    install(monkeypatch, scripted)
    state = run_extraction("BidT", run_id="t-empty")
    record = state["record"]
    extracted = {n: v for n, v in record.fields.items() if n != "Bid Summary"}
    assert all(v.status == "not_found" and v.value is None for v in extracted.values())
    assert all(v.notes == "Not found in documents" for v in extracted.values())
    assert "FieldExtractions" not in scripted.calls and "ReconcileDecisions" not in scripted.calls
    assert record.validation == {"passed": 0, "failed": 0, "not_found": 20}


def test_engine_failure_in_retrieval_is_contained(workspace, monkeypatch):
    class BrokenEngine(FakeEngine):
        def search(self, *a, **k):
            raise RuntimeError("qdrant lock held by another process")

    runtime.set_engine(BrokenEngine())
    install(monkeypatch, ScriptedLLM())
    state = run_extraction("BidT", run_id="t-broken")
    assert state["record"] is not None
    assert all(v.status == "not_found" for n, v in state["record"].fields.items() if n != "Bid Summary")
    trace = read_trace("t-broken")
    assert any(c.get("error") for s in trace for c in s.tool_calls if c["kind"] == "search")


def test_qa_llm_failure_returns_not_found(workspace, monkeypatch):
    runtime.set_engine(FakeEngine())
    install(monkeypatch, ScriptedLLM(fail=llm.LLMError("timeout")))
    state = run_question("What is the due date?", ["BidT"], run_id="t-qa-fail")
    assert state["answer"].answer == "Not found in documents"
    assert any("LLM call failed" in e for e in state["errors"])
