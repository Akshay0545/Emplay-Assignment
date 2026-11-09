"""CLI end to end on the mini bid with a fake engine and a scripted LLM (no network, no models)."""

import json

import pytest
from typer.testing import CliRunner

import main
from rfp_intel.agents import runtime
from rfp_intel.config import field_names, settings
from tests.graph_fakes import FakeEngine, ScriptedLLM, install

runner = CliRunner(env={"COLUMNS": "250"})


@pytest.fixture
def cli_env(tmp_path, mini_bid, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    (data / "BidT").mkdir()
    for path in mini_bid.iterdir():
        (data / "BidT" / path.name).write_bytes(path.read_bytes())
    monkeypatch.setattr(settings.paths, "data_dir", str(data))
    monkeypatch.setattr(settings.paths, "outputs_dir", str(tmp_path / "outputs"))
    monkeypatch.setattr(settings.paths, "logs_dir", str(tmp_path / "logs"))
    monkeypatch.setattr(settings.env, "extract_model", "fake-extract")
    monkeypatch.setattr(settings.env, "reason_model", "fake-reason")
    engine = FakeEngine()
    runtime.set_engine(engine)
    monkeypatch.setattr(runtime, "close_engine", lambda: None)   # keep the fake across commands
    install(monkeypatch, ScriptedLLM())
    yield tmp_path, engine
    runtime.set_engine(None)


def test_help_lists_all_commands():
    result = runner.invoke(main.app, ["--help"])
    assert result.exit_code == 0
    for command in ("parse", "index", "search", "extract", "ask", "run", "compare", "eval"):
        assert command in result.output


def test_parse_writes_json_and_table(cli_env):
    tmp_path, _ = cli_env
    result = runner.invoke(main.app, ["parse", "--bid", "BidT"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "outputs" / "parsed" / "BidT" / "rfp.pdf.json").exists()
    assert "addendum" in result.output and "bid_page" in result.output


def test_index_and_search(cli_env):
    assert runner.invoke(main.app, ["index", "--bid", "BidT"]).exit_code == 0
    result = runner.invoke(main.app, ["search", "due date", "--bid", "BidT", "--top-k", "2"])
    assert result.exit_code == 0 and "addendum_2.pdf" in result.output
    assert runner.invoke(main.app, ["search", "x", "--config", "nope"]).exit_code == 2


def test_default_bid_runs_extraction_graph(cli_env):
    tmp_path, _ = cli_env
    result = runner.invoke(main.app, ["--bid", str(tmp_path / "data" / "BidT")])
    assert result.exit_code == 0, result.output
    record = json.loads((tmp_path / "outputs" / "BidT.json").read_text(encoding="utf-8"))
    assert set(record["fields"]) == set(field_names())
    assert "addendum change: Due Date (override, Addendum 2)" in result.output
    assert "trace=" in result.output


def test_extract_baseline_flag(cli_env):
    tmp_path, _ = cli_env
    result = runner.invoke(main.app, ["extract", "--bid", "BidT", "--baseline"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "outputs" / "BidT.json").exists()


def test_ask_prints_answer_with_citations(cli_env):
    result = runner.invoke(main.app, ["ask", "What is the due date?", "--bid", "BidT"])
    assert result.exit_code == 0, result.output
    assert "July 9, 2024" in result.output and "[1] addendum_2.pdf p.1" in result.output


def test_missing_bid_folder_exits_2(cli_env):
    assert runner.invoke(main.app, ["parse", "--bid", "NoSuchBid"]).exit_code == 2
