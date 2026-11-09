"""Comparison and Go/No-Go agents (bonus) with a scripted LLM: rows, highlights,
rule-decided criteria, the recommendation logic, written files, CLI and API."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

import main
from rfp_intel import services
from rfp_intel.agents import runtime
from rfp_intel.agents.graph import run_comparison, run_go_no_go
from rfp_intel.agents.nodes import go_no_go as gng
from rfp_intel.api.server import app
from rfp_intel.config import Capabilities, Criterion, field_names, load_capabilities, settings
from rfp_intel.logging_utils import read_trace
from rfp_intel.schemas import BidRecord, Citation, CriterionResult, FieldValue
from tests.graph_fakes import FakeEngine, ScriptedLLM, install

CITE = Citation(file="rfp.pdf", page=2, chunk_id="r:2:0", quote="due June 27, 2024 at 2:00 PM CST")


def _record(bid_id: str, values: dict[str, str | None]) -> BidRecord:
    """A saved-record shaped BidRecord: the given fields ok, the rest not found."""
    fields = {}
    for name in field_names():
        value = values.get(name)
        if value is None:
            fields[name] = FieldValue(value=None, status="not_found", confidence=0.5, notes="Not found in documents")
        else:
            fields[name] = FieldValue(value=value, sources=[CITE], confidence=0.9, status="ok")
    counts = {"passed": sum(v.status == "ok" for v in fields.values()), "failed": 0,
              "not_found": sum(v.status == "not_found" for v in fields.values())}
    return BidRecord(bid_id=bid_id, fields=fields, validation=counts, generated_at="2026-10-11T00:00:00Z", run_id="r-0")


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Temp dirs, two saved records (BidT and BidU), a fake engine and a scripted LLM."""
    data = tmp_path / "data"
    for bid in ("BidT", "BidU"):
        (data / bid).mkdir(parents=True)
    out = tmp_path / "outputs"
    out.mkdir()
    monkeypatch.setattr(settings.paths, "data_dir", str(data))
    monkeypatch.setattr(settings.paths, "outputs_dir", str(out))
    monkeypatch.setattr(settings.paths, "logs_dir", str(tmp_path / "logs"))
    monkeypatch.setattr(settings.env, "extract_model", "fake-extract")
    monkeypatch.setattr(settings.env, "reason_model", "fake-reason")
    monkeypatch.setattr(services, "EVAL_RESULTS_DIR", tmp_path / "eval_results")
    bid_t = _record("BidT", {"Due Date": "July 9, 2024 at 2:00 PM CST", "Product": "100 Model X laptops",
                             "MFG for Registration": "Dell", "Delivery Date": "within 45 days of award",
                             "Term of Bid": "three years", "company_name": "Example County, Texas"})
    bid_u = _record("BidU", {"Due Date": "June 10, 2024 02:00 PM EDT", "Product": "100 Model X laptops",
                             "Bid Bond Requirement": "5% of the bid"})
    for record in (bid_t, bid_u):
        (out / f"{record.bid_id}.json").write_text(record.model_dump_json(indent=2), encoding="utf-8")
    runtime.set_engine(FakeEngine())
    scripted = ScriptedLLM()
    install(monkeypatch, scripted)
    services._runs.clear()
    yield tmp_path, scripted
    runtime.set_engine(None)


# ---------------------------------------------------------------- comparison


def test_comparison_rows_highlights_and_files(workspace):
    tmp_path, scripted = workspace
    state = run_comparison(["BidT", "BidU"], run_id="t-cmp")
    report = state["comparison"]

    assert report.bid_ids == ["BidT", "BidU"] and [r.field for r in report.rows] == field_names()
    due = next(r for r in report.rows if r.field == "Due Date")
    assert due.differs and [c.bid_id for c in due.cells] == ["BidT", "BidU"]
    assert due.cells[0].sources == [CITE]
    product = next(r for r in report.rows if r.field == "Product")
    assert not product.differs                              # same value in both bids
    assert not next(r for r in report.rows if r.field == "Part_no").differs   # missing in both
    assert "due dates differ" in report.narrative
    assert [h.field for h in report.highlights] == ["Due Date"]   # unknown field dropped
    assert report.highlights[0].sources == [CITE]
    assert state["errors"] == []
    assert scripted.calls == ["ComparisonDraft"]

    saved = json.loads((tmp_path / "outputs" / "comparison.json").read_text(encoding="utf-8"))
    assert saved["run_id"] == "t-cmp"
    md = (tmp_path / "outputs" / "comparison.md").read_text(encoding="utf-8")
    assert "| Due Date |" in md and "BidT vs BidU" in md and "rfp.pdf p.2" in md
    assert [s.node for s in read_trace("t-cmp")] == ["compare"]


def test_comparison_survives_llm_failure(workspace, monkeypatch):
    install(monkeypatch, ScriptedLLM(fail=RuntimeError("boom")))
    state = run_comparison(["BidT", "BidU"], run_id="t-cmp-fail")
    report = state["comparison"]
    assert len(report.rows) == len(field_names()) and report.highlights == []
    assert report.narrative.startswith("Narrative unavailable")
    assert state["errors"] and "boom" in state["errors"][0]


def test_compare_service_validates_inputs(workspace):
    with pytest.raises(ValueError):
        services.compare_bids(["BidT", "BidT"])
    with pytest.raises(FileNotFoundError):
        services.compare_bids(["BidT", "Nope"])
    assert services.latest_comparison() is None
    services.compare_bids(["BidT", "BidU"])
    assert services.latest_comparison().bid_ids == ["BidT", "BidU"]


# ---------------------------------------------------------------- go / no-go


def _caps(**overrides) -> Capabilities:
    return load_capabilities().model_copy(update=overrides)


def test_go_no_go_rule_decided_and_llm_criteria(workspace):
    tmp_path, scripted = workspace
    state = run_go_no_go("BidT", run_id="t-gng")
    report = state["go_no_go"]
    by_id = {c.criterion: c for c in report.criteria}

    assert set(by_id) == {c.id for c in load_capabilities().criteria}
    # Bid bond not stated -> the configured when_not_found (met) without asking the LLM.
    assert by_id["bonding"].status == "met" and by_id["bonding"].reason.startswith("None of the fields")
    assert by_id["bonding"].sources == []
    # Submission type not stated -> unknown by rule.
    assert by_id["submission"].status == "unknown"
    # Criteria with values were judged by the LLM and carry the fields' citations.
    assert by_id["manufacturer_authorization"].status == "met" and by_id["manufacturer_authorization"].sources == [CITE]
    assert by_id["delivery"].critical and by_id["delivery"].status == "met"
    assert scripted.calls == ["CriterionChecks"]
    assert report.recommendation == "review"           # unknown non-critical criteria remain
    assert report.met + report.not_met + report.unknown == len(report.criteria)
    assert "Needs review" in report.summary and state["errors"] == []

    assert (tmp_path / "outputs" / "BidT_go_no_go.json").exists()
    md = (tmp_path / "outputs" / "BidT_go_no_go.md").read_text(encoding="utf-8")
    assert md.startswith("# BidT: Needs review") and "config/capabilities.yaml" in md
    assert [s.node for s in read_trace("t-gng")] == ["go_no_go"]


def test_go_no_go_no_go_on_critical_miss(workspace, monkeypatch):
    install(monkeypatch, ScriptedLLM(criterion_verdicts={"manufacturer_authorization": "not_met"}))
    report = run_go_no_go("BidT", run_id="t-gng-nogo")["go_no_go"]
    assert report.recommendation == "no_go"
    assert "Not met: Authorized for the required manufacturer(s)" in report.summary


def test_go_no_go_llm_failure_marks_unknown(workspace, monkeypatch):
    install(monkeypatch, ScriptedLLM(fail=RuntimeError("timeout")))
    state = run_go_no_go("BidT", run_id="t-gng-fail")
    report = state["go_no_go"]
    asked = [c for c in report.criteria if c.criterion in ("manufacturer_authorization", "delivery")]
    assert all(c.status == "unknown" and "timeout" in c.reason for c in asked)
    assert report.recommendation == "review" and state["errors"]


def test_recommendation_rules():
    def crit(cid, status, critical):
        return CriterionResult(criterion=cid, label=cid, status=status, critical=critical)

    caps = _caps()
    assert gng.recommend([crit("a", "met", True), crit("b", "met", False)], caps) == "go"
    assert gng.recommend([crit("a", "met", True), crit("b", "not_met", False)], caps) == "review"
    assert gng.recommend([crit("a", "unknown", True)], caps) == "review"
    assert gng.recommend([crit("a", "not_met", True), crit("b", "met", False)], caps) == "no_go"
    strict = caps.model_copy(update={"decision": caps.decision.model_copy(update={"unknown_critical": "no_go"})})
    assert gng.recommend([crit("a", "unknown", True)], strict) == "no_go"


def test_capabilities_config_is_well_formed():
    caps = load_capabilities()
    ids = [c.id for c in caps.criteria]
    assert len(ids) == len(set(ids)) and all(isinstance(c, Criterion) for c in caps.criteria)
    known = set(field_names())
    for crit in caps.criteria:
        assert crit.fields and set(crit.fields) <= known, crit.id
        assert crit.rule.strip() and crit.label


# ---------------------------------------------------------------- CLI and API


runner = CliRunner(env={"COLUMNS": "250"})


def test_cli_compare_and_go_no_go(workspace, monkeypatch):
    tmp_path, _ = workspace
    monkeypatch.setattr(runtime, "close_engine", lambda: None)
    result = runner.invoke(main.app, ["compare", "BidT", "BidU"])
    assert result.exit_code == 0, result.output
    assert "Due Date" in result.output and "due dates differ" in result.output
    assert runner.invoke(main.app, ["compare", "BidT", "Nope"]).exit_code == 2

    result = runner.invoke(main.app, ["go-no-go", "--bid", "BidT"])
    assert result.exit_code == 0, result.output
    assert "Needs review" in result.output and "Bid bond within bonding capacity" in result.output
    assert runner.invoke(main.app, ["go-no-go", "--bid", "BidU"]).exit_code == 0   # not_met bond -> still runs
    assert runner.invoke(main.app, ["go-no-go", "--bid", "Missing"]).exit_code == 2


def test_api_compare_and_go_no_go(workspace):
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/compare").status_code == 404
        assert client.post("/compare", json={"bid_ids": ["BidT"]}).status_code == 400
        assert client.post("/compare", json={"bid_ids": ["BidT", "Nope"]}).status_code == 404
        body = client.post("/compare", json={"bid_ids": ["BidT", "BidU"]}).json()
        assert body["bid_ids"] == ["BidT", "BidU"] and len(body["rows"]) == len(field_names())
        assert client.get("/compare").json()["run_id"] == body["run_id"]
        assert client.get(f"/runs/{body['run_id']}").json()["kind"] == "compare"
        listed = {r["run_id"]: r for r in client.get("/runs").json()}
        assert listed[body["run_id"]]["kind"] == "compare" and listed[body["run_id"]]["bid_id"] == "BidT,BidU"
        assert client.get(f"/runs/{body['run_id']}").json()["bid_id"] == "BidT,BidU"

        assert client.get("/bids/BidT/go-no-go").status_code == 404
        assert client.post("/bids/Nope/go-no-go").status_code == 404
        report = client.post("/bids/BidT/go-no-go").json()
        assert report["recommendation"] == "review" and len(report["criteria"]) == len(load_capabilities().criteria)
        assert client.get("/bids/BidT/go-no-go").json()["run_id"] == report["run_id"]
        assert client.get(f"/runs/{report['run_id']}").json()["kind"] == "go_no_go"
        assert client.get(f"/runs/{report['run_id']}").json()["bid_id"] == "BidT"
        assert client.get("/metrics").json()["runs"]["go_no_go"]["done"] == 1


def test_run_status_from_trace_resolves_bid_ids(workspace):
    cmp = services.compare_bids(["BidT", "BidU"])
    gng = services.assess_go_no_go("BidU")
    services._runs.clear()
    assert services.run_status(cmp.run_id).bid_id == "BidT,BidU"
    later = services.compare_bids(["BidU", "BidT"])          # overwrites outputs/comparison.json
    services._runs.clear()
    assert services.run_status(cmp.run_id).bid_id == "BidT,BidU"
    assert services.run_status(later.run_id).bid_id == "BidU,BidT"
    assert services.run_status(gng.run_id).bid_id == "BidU"
    assert services.run_status(gng.run_id).kind == "go_no_go"
