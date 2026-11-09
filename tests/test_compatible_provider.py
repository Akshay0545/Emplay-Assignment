"""OpenAI-compatible providers (gemini, groq, openrouter, ollama) and the reconcile old_value guard."""

import pytest
from pydantic import BaseModel

from rfp_intel import llm
from rfp_intel.agents.nodes.reconcile import _old_value
from rfp_intel.config import settings
from rfp_intel.schemas import ReconcileDecision


class DueDate(BaseModel):
    date: str


class Recorder:
    """Fake provider that records the schema / schema name it was sent."""

    def __init__(self, reply: str):
        self.reply = reply
        self.calls: list[tuple[dict | None, str, str | None]] = []

    def complete(self, prompt, system, model, schema, schema_name):
        self.calls.append((schema, schema_name, system))
        return llm.RawResponse(text=self.reply, tokens_in=5, tokens_out=5)


@pytest.fixture
def gemini(monkeypatch):
    monkeypatch.setattr(settings.env, "llm_provider", "gemini")
    monkeypatch.setattr(settings.env, "llm_api_key", llm.SecretStr("k"))
    monkeypatch.setattr(settings.env, "llm_dev_cache", False)
    llm.reset_fallback()
    rec = Recorder('{"date": "2024-07-09"}')
    monkeypatch.setattr(llm, "_provider", lambda name=None: rec)
    yield rec
    llm.reset_fallback()


def test_compatible_provider_uses_json_mode_with_schema_hint(gemini):
    obj, _ = llm.structured("p", DueDate, "gemini-x")
    assert obj.date == "2024-07-09"
    schema, schema_name, system = gemini.calls[0]
    assert schema is None                       # no strict json_schema sent
    assert schema_name == "json:DueDate"        # JSON-object mode requested
    assert '"date"' in system                   # schema travels in the system prompt


@pytest.mark.parametrize("name,url", [("gemini", "generativelanguage"), ("groq", "groq.com"),
                                      ("openrouter", "openrouter.ai"), ("ollama", "localhost:11434")])
def test_compatible_presets_build_client(monkeypatch, name, url):
    monkeypatch.setattr(settings.env, "llm_provider", name)
    monkeypatch.setattr(settings.env, "llm_api_key", llm.SecretStr("k"))
    monkeypatch.setattr(settings.env, "llm_base_url", "")
    provider = llm._provider(name)
    assert isinstance(provider, llm.CompatibleProvider)
    assert url in str(provider._client.base_url)


def test_base_url_override(monkeypatch):
    monkeypatch.setattr(settings.env, "llm_provider", "compatible")
    monkeypatch.setattr(settings.env, "llm_api_key", llm.SecretStr("k"))
    monkeypatch.setattr(settings.env, "llm_base_url", "http://example.test/v1")
    assert "example.test" in str(llm._provider("compatible")._client.base_url)


def test_unknown_provider_lists_options(monkeypatch):
    monkeypatch.setattr(settings.env, "llm_provider", "nope")
    monkeypatch.setattr(settings.env, "llm_api_key", llm.SecretStr("k"))
    with pytest.raises(llm.LLMError, match="gemini"):
        llm._provider("nope")


def _decision(old: str | None, new: str) -> ReconcileDecision:
    return ReconcileDecision(field="Due Date", kind="override", new_value=new, old_value=old,
                             evidence_id="E1", quote="", reason="")


def test_old_value_never_echoes_new_value():
    new = "July 9, 2024 at 2:00 PM CST"
    notes = "As written: July 9, 2024 at 2:00 PM CST. Original due date was 27-JUN-2024 14:00:00."
    assert _old_value(_decision("July 9, 2024 at 2:00 PM CST.", new), new, notes) == "27-JUN-2024 14:00:00"
    assert _old_value(_decision(None, new), new, "") is None
    assert _old_value(_decision("June 27, 2024", new), new, notes) == "June 27, 2024"
    assert _old_value(_decision(None, new), "June 27, 2024 2 PM", "") == "June 27, 2024 2 PM"
