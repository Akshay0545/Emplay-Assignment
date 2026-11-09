"""Validator corrections: partial support of list values, logged verdicts, note hygiene, date normalization."""

from __future__ import annotations

import pytest

from rfp_intel.agents import runtime
from rfp_intel.agents.graph import run_extraction
from rfp_intel.agents.nodes import reconcile
from rfp_intel.agents.nodes.report import normalize_value
from rfp_intel.agents.nodes.validate import drop_unsupported_items
from rfp_intel.agents.rules import format_datetime
from rfp_intel.config import settings
from rfp_intel.logging_utils import read_trace
from rfp_intel.schemas import FieldValue, SupportCheck
from tests.graph_fakes import FakeEngine, ScriptedLLM, install


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(settings.paths, "outputs_dir", str(tmp_path / "outputs"))
    monkeypatch.setattr(settings.paths, "logs_dir", str(tmp_path / "logs"))
    monkeypatch.setattr(settings.env, "extract_model", "fake-extract")
    monkeypatch.setattr(settings.env, "reason_model", "fake-reason")
    runtime.set_engine(FakeEngine())
    yield tmp_path
    runtime.set_engine(None)


def _value(items, notes=""):
    return FieldValue(value=items, confidence=0.5, notes=notes)


# ---------------------------------------------------------------- drop_unsupported_items

def test_partial_list_keeps_supported_items_and_names_the_dropped_ones():
    value = _value(["W-9 form", "Form 1295", "Letter of Intent"])
    check = SupportCheck(field="x", supported=False, unsupported_items=["Letter of Intent"])
    trimmed = drop_unsupported_items(value, check)
    assert trimmed is not None
    assert trimmed.value == ["W-9 form", "Form 1295"]
    assert "Validator removed 1 unsupported item(s): Letter of Intent." in trimmed.notes


def test_matching_ignores_case_and_whitespace():
    value = _value(["Contract Affidavit (signed)", "Mercury Affidavit"])
    check = SupportCheck(field="x", supported=False, unsupported_items=["mercury affidavit"])
    assert drop_unsupported_items(value, check).value == ["Contract Affidavit (signed)"]


@pytest.mark.parametrize("value, items", [
    ("a single string", ["a single string"]),           # not a list: the whole field fails as before
    (["only item"], ["only item"]),                     # every item unsupported: nothing to keep
    (["a", "b"], ["zzz"]),                              # the reported item matches nothing
    (["a", "b"], []),                                   # unsupported without items: whole field fails
])
def test_no_correction_when_it_would_hide_a_real_failure(value, items):
    check = SupportCheck(field="x", supported=False, unsupported_items=items)
    assert drop_unsupported_items(_value(value), check) is None


def test_reported_items_joined_by_semicolons_are_split_back():
    value = _value(["W-9 form", "Form 1295", "Letter of Intent"])
    check = SupportCheck(field="x", supported=False, unsupported_items=["Form 1295; Letter of Intent"])
    assert drop_unsupported_items(value, check).value == ["W-9 form"]
    whole = SupportCheck(field="x", supported=False, unsupported_items=["W-9 form; Form 1295; Letter of Intent"])
    assert drop_unsupported_items(value, whole) is None          # every item rejected: whole field fails


def test_item_containing_a_semicolon_is_matched_as_one_item():
    value = _value(["CPU: i7; 32GB RAM", "Display 13-inch"])
    check = SupportCheck(field="x", supported=False, unsupported_items=["CPU: i7; 32GB RAM"])
    assert drop_unsupported_items(value, check).value == ["Display 13-inch"]


def test_support_prompt_shows_list_values_as_json_arrays(workspace, monkeypatch):
    from rfp_intel.agents.nodes.validate import support_checks
    captured = {}

    def fake_structured(prompt, model_cls, model, system=None):
        captured["prompt"] = prompt
        raise RuntimeError("stop")

    monkeypatch.setattr("rfp_intel.agents.nodes.validate.llm.structured", fake_structured)
    fields = {"Product": _value(["a; b", "c"]), "Title": _value("one string")}
    support_checks("BidT", "g", fields, {})
    assert 'value=["a; b", "c"]' in captured["prompt"] and "value='one string'" in captured["prompt"]


def test_supported_value_is_untouched():
    check = SupportCheck(field="x", supported=True, unsupported_items=["a"])
    assert drop_unsupported_items(_value(["a", "b"]), check) is None


# ---------------------------------------------------------------- in the graph

def test_graph_corrects_a_partially_supported_list_without_retrying(workspace, monkeypatch):
    product = (["100 Model X laptops", "50 Model Y tablets"], "100 Model X laptops")
    install(monkeypatch, ScriptedLLM(overrides={"Product": [product]},
                                     unsupported_items={"Product": ["50 Model Y tablets"]}))
    state = run_extraction("BidT", run_id="t-partial")
    field = state["record"].fields["Product"]
    assert field.status == "ok" and field.value == ["100 Model X laptops"]
    assert "Validator removed 1 unsupported item(s): 50 Model Y tablets." in field.notes
    assert state["retries"].get("Product", 0) == 0 and state["validation"]["Product"].passed
    steps = [s for s in read_trace("t-partial") if s.node == "validate"]
    assert len(steps) == 1
    logged = [c for c in steps[0].tool_calls if c["kind"] == "support_check"]
    assert logged, "support-check verdicts are part of the trace"
    verdict = next(c["verdicts"]["Product"] for c in logged if "Product" in c["verdicts"])
    assert verdict == {"supported": False, "reason": "item not in evidence", "unsupported_items": ["50 Model Y tablets"]}


# ---------------------------------------------------------------- reconcile notes

@pytest.mark.parametrize("note, existing, expected", [
    ("Clarified by Addendum 1: Form 1295 is due with the response.", "Form 1295 is due with the response.",
     "Clarified by Addendum 1: Form 1295 is due with the response."),
    ("Changed by Addendum 2: originally June 27.", "Not found in documents", "Changed by Addendum 2: originally June 27."),
    ("Changed by Addendum 2: originally June 27.", "", "Changed by Addendum 2: originally June 27."),
    ("Addendum 1 adds: W-9.", "Insurance form due after award.", "Addendum 1 adds: W-9. Insurance form due after award."),
    ("Addendum 1 adds: W-9.", "Addendum 1 adds: W-9. Insurance form due after award.",
     "Addendum 1 adds: W-9. Insurance form due after award."),
])
def test_join_notes_never_repeats_text(note, existing, expected):
    assert reconcile._join_notes(note, existing) == expected


# ---------------------------------------------------------------- date normalization

@pytest.mark.parametrize("text, expected", [
    ("July 9, 2024 at 2:00 PM CST", "2024-07-09 14:00 CST"),
    ("06/10/2024 02:00 PM EDT", "2024-06-10 14:00 EDT"),
    ("June 10, 2024 2:00 PM Central", "2024-06-10 14:00 CT"),
    ("2:00 p.m. Eastern Time on July 9, 2024", "2024-07-09 14:00 ET"),
    ("March 3, 2025", "2025-03-03"),
    ("July 9, 2024 at 14:30", "2024-07-09 14:30"),
    ("no date here", None),
])
def test_format_datetime(text, expected):
    assert format_datetime(text) == expected


def test_normalize_value_keeps_wording_in_notes_and_skips_what_it_cannot_parse():
    ok = FieldValue(value="July 9, 2024 at 2:00 PM CST", confidence=0.9, notes="Changed by Addendum 2.")
    out = normalize_value(ok, "datetime")
    assert out.value == "2024-07-09 14:00 CST"
    assert out.notes == "As written: July 9, 2024 at 2:00 PM CST. Changed by Addendum 2."
    assert out.sources == ok.sources and out.confidence == ok.confidence
    assert normalize_value(ok, None) is ok
    assert normalize_value(FieldValue(value="upon award", confidence=0.5), "datetime").value == "upon award"
    failed = FieldValue(value="July 9, 2024", confidence=0.2, status="failed")
    assert normalize_value(failed, "datetime").value == "July 9, 2024"
    assert normalize_value(FieldValue(value=None, confidence=0.5, status="not_found"), "datetime").value is None


def test_record_due_date_is_normalized_in_the_graph(workspace, monkeypatch):
    install(monkeypatch, ScriptedLLM())
    state = run_extraction("BidT", run_id="t-normalized")
    due = state["record"].fields["Due Date"]
    assert due.value == "2024-07-09 14:00 CST" and due.status == "ok"
    assert due.notes.startswith("As written: July 9, 2024 at 2:00 PM CST.")
    assert "Changed by Addendum 2" in due.notes


# ---------------------------------------------------------------- grounding guard

def test_grounded_item_survives_a_false_unsupported_verdict():
    evidence = ["Memory: 16 GB, 2 x 8 GB, DDR5, 5600 MT/s. Display: 15.6 inch FHD non-touch."]
    value = _value(["RAM: 16 GB DDR5 5600 MT/s", "Display: 15.6 FHD non-touch", "Webcam: 4K HDR"])
    check = SupportCheck(field="x", supported=False,
                         unsupported_items=["RAM: 16 GB DDR5 5600 MT/s", "Webcam: 4K HDR"])
    trimmed = drop_unsupported_items(value, check, evidence)
    assert trimmed is not None
    assert trimmed.value == ["RAM: 16 GB DDR5 5600 MT/s", "Display: 15.6 FHD non-touch"]
    assert "Webcam: 4K HDR" in trimmed.notes


def test_without_evidence_texts_the_verdict_is_taken_as_is():
    value = _value(["a", "b"])
    check = SupportCheck(field="x", supported=False, unsupported_items=["b"])
    assert drop_unsupported_items(value, check).value == ["a"]

