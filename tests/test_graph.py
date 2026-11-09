"""Extraction and Q&A graphs with a fake engine and a scripted LLM."""

import json

import pytest

from rfp_intel.agents import runtime
from rfp_intel.agents.graph import run_extraction, run_question
from rfp_intel.agents.nodes.retrieve import field_queries, field_top_k
from rfp_intel.config import FieldSpec, field_names, settings
from rfp_intel.logging_utils import read_trace
from rfp_intel.schemas import BidRecord
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


def _steps(run_id):
    return [(s.node, s.started_at, s.agent) for s in read_trace(run_id)]


def test_extraction_graph_end_to_end(workspace, monkeypatch):
    engine = FakeEngine()
    runtime.set_engine(engine)
    install(monkeypatch, ScriptedLLM())
    state = run_extraction("BidT", run_id="t-e2e")
    record: BidRecord = state["record"]

    assert set(record.fields) == set(field_names())
    due = record.fields["Due Date"]
    assert due.value == "2024-07-09 14:00 CST" and due.status == "ok"     # normalized; wording kept in notes
    assert "As written: July 9, 2024 at 2:00 PM CST" in due.notes
    assert due.sources[0].file == "addendum_2.pdf" and "July 9, 2024" in due.sources[0].quote
    assert "originally June 27, 2024" in due.notes
    assert [c.kind for c in record.addendum_changes] == ["override"]
    assert record.fields["Bid Bond Requirement"].status == "not_found"
    assert record.fields["Bid Bond Requirement"].notes == "Not found in documents"
    assert all(v.sources for v in record.fields.values() if v.value is not None)
    assert record.validation["passed"] + record.validation["failed"] + record.validation["not_found"] == 20
    assert state["errors"] == []

    saved = json.loads((workspace / "outputs" / "BidT.json").read_text(encoding="utf-8"))
    assert saved["bid_id"] == "BidT" and (workspace / "outputs" / "BidT_summary.md").exists()

    nodes = [n for n, _, _ in _steps("t-e2e")]
    assert nodes[:2] == ["ingest", "orchestrate"] and nodes[-1] == "report"
    extract_steps = [s for s in read_trace("t-e2e") if s.node.startswith("extract_")]
    assert len(extract_steps) == 3
    for step in extract_steps:
        earlier = [r for r in read_trace("t-e2e") if r.node == "retrieve" and r.started_at <= step.started_at]
        assert earlier, "retrieve must run before every extraction"
    assert any(s.node == "retrieve" and s.tool_calls and s.tool_calls[0]["kind"] == "search" for s in read_trace("t-e2e"))
    assert all(s.node != "retrieve" or "search" in {c["kind"] for c in s.tool_calls} for s in read_trace("t-e2e"))


def test_validator_retry_path(workspace, monkeypatch):
    runtime.set_engine(FakeEngine())
    bad = ("sometime soon", "due June 27, 2024 at 2:00 PM CST")      # fails format_ok once
    install(monkeypatch, ScriptedLLM(overrides={"Pre Bid Meeting": [bad]}))
    state = run_extraction("BidT", run_id="t-retry")
    assert state["retries"].get("Pre Bid Meeting") == 1
    nodes = [n for n, _, _ in _steps("t-retry")]
    assert nodes.count("validate") == 2 and nodes.count("retrieve") == 4
    assert nodes.count("extract_dates_logistics") == 2 and nodes.count("extract_product_specs") == 1
    retry_step = [s for s in read_trace("t-retry") if s.node == "retrieve"][-1]
    assert retry_step.tool_calls[0]["top_k"] == settings.agents.widened_top_k
    assert state["record"].fields["Pre Bid Meeting"].status == "not_found"
    assert state["validation"]["Pre Bid Meeting"].passed


def test_retries_exhausted_marks_field_failed(workspace, monkeypatch):
    runtime.set_engine(FakeEngine())
    bad = [("sometime soon", "due June 27, 2024 at 2:00 PM CST")] * 5
    install(monkeypatch, ScriptedLLM(overrides={"Pre Bid Meeting": bad}))
    state = run_extraction("BidT", run_id="t-exhausted")
    delivery = state["record"].fields["Pre Bid Meeting"]
    assert state["retries"]["Pre Bid Meeting"] == settings.agents.max_field_retries
    assert delivery.status == "failed" and delivery.confidence <= 0.2 and "format_ok" in delivery.notes
    assert state["record"].validation["failed"] == 1


def test_qa_graph_answers_with_citations(workspace, monkeypatch):
    runtime.set_engine(FakeEngine())
    install(monkeypatch, ScriptedLLM())
    state = run_question("What is the due date?", ["BidT"], run_id="t-qa")
    ans = state["answer"]
    assert "July 9, 2024" in ans.answer and ans.citations and ans.citations[0].file == "addendum_2.pdf"
    assert ans.bids == ["BidT"] and ans.confidence >= 0.5
    assert [n for n, _, _ in _steps("t-qa")] == ["orchestrate", "retrieve", "answer", "validate_answer"]


def test_qa_graph_not_found_without_evidence(workspace, monkeypatch):
    runtime.set_engine(FakeEngine(empty=True))
    scripted = ScriptedLLM()
    install(monkeypatch, scripted)
    state = run_question("Is a bid bond required?", ["BidT"], run_id="t-qa-empty")
    assert state["answer"].answer == "Not found in documents" and state["answer"].citations == []
    assert "AnswerDraft" not in scripted.calls


def test_qa_retry_redrafts_on_same_evidence(workspace, monkeypatch):
    engine = FakeEngine()
    runtime.set_engine(engine)
    scripted = ScriptedLLM(reject_answer_once=True)
    install(monkeypatch, scripted)
    state = run_question("What is the due date?", ["BidT"], run_id="t-qa-retry")
    nodes = [n for n, _, _ in _steps("t-qa-retry")]
    assert nodes == ["orchestrate", "retrieve", "answer", "validate_answer", "answer", "validate_answer"]
    assert state["retries"] == {"answer": 1} and "July 9, 2024" in state["answer"].answer
    searches_after_first_retrieve = len(engine.searches)
    assert searches_after_first_retrieve == 1            # one query from the plan, searched once
    drafts = [prompt for name, prompt in scripted.prompts if name == "AnswerDraft"]
    assert len(drafts) == 2 and "rejected" not in drafts[0]
    assert "rejected by the validator: the evidence does not mention a portal" in drafts[1]
    assert state["answer"].confidence >= 0.5


def test_change_log_queries_follow_the_question_topic(workspace, monkeypatch):
    from rfp_intel.agents.nodes.retrieve import change_log_queries

    runtime.set_engine(FakeEngine())
    install(monkeypatch, ScriptedLLM())
    run_extraction("BidT", run_id="t-changes")             # saves outputs/BidT.json with a Due Date override
    assert change_log_queries("BidT", ["due date", "deadline extended by addendum"]) == [
        "Due Date June 27, 2024 at 2:00 PM CST", "addendum 2 Due Date July 9, 2024 at 2:00 PM CST"]
    assert change_log_queries("BidT", ["warranty terms in the addendum"]) == []     # unrelated change skipped
    assert change_log_queries("BidT", ["due date"]) == []                           # no addendum mentioned
    assert change_log_queries("NoSuchBid", ["due date addendum"]) == []


def test_retrieve_searches_each_request_once(workspace, monkeypatch):
    engine = FakeEngine()
    runtime.set_engine(engine)
    install(monkeypatch, ScriptedLLM())
    run_question("What is the due date?", ["BidT"], run_id="t-qa-once")
    step = [s for s in read_trace("t-qa-once") if s.node == "retrieve"][0]
    searches = [c for c in step.tool_calls if c["kind"] == "search"]
    assert len(searches) == 1 and searches[0]["queries"] == ["due date"] and searches[0]["hits"] == 3


def test_field_top_k_prefers_the_field_setting(monkeypatch):
    monkeypatch.setattr(settings.agents, "evidence_top_k", 6)
    monkeypatch.setattr(settings.agents, "widened_top_k", 10)
    plain = FieldSpec(name="Title", group="g", description="d")
    deep = FieldSpec(name="Docs", group="g", description="d", top_k=12)
    assert field_top_k(plain) == 6 and field_top_k(plain, widened=True) == 10
    assert field_top_k(deep) == 12 and field_top_k(deep, widened=True) == 12


def test_field_queries_cover_the_later_hints_in_small_groups():
    spec = FieldSpec(name="Docs", group="g", description="Documents bidders must submit.",
                     search_hints=["affidavit", "form", "certificate", "W-9", "insurance", "references",
                                   "attachment", "price list"])
    queries = field_queries(spec)
    assert len(queries) == 4
    assert queries[0].startswith("Docs affidavit form certificate W-9")
    assert queries[2:] == ["insurance references attachment", "price list"]
