"""Provider fallback: missing or rejected primary key -> the fallback provider and its models."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, SecretStr

from rfp_intel import llm
from rfp_intel.config import settings


class Reply(BaseModel):
    text: str


class RecordingProvider:
    """Fake provider that records (model) per call and can raise on demand."""

    def __init__(self, name: str, fail: Exception | None = None):
        self.name, self.fail = name, fail
        self.models: list[str] = []

    def complete(self, prompt, system, model, schema, schema_name):
        self.models.append(model)
        if self.fail is not None:
            raise self.fail
        return llm.RawResponse(text='{"text": "ok from ' + self.name + '"}', tokens_in=10, tokens_out=5)


@pytest.fixture
def env(monkeypatch):
    """Primary openai with EXTRACT/REASON models, fallback anthropic with its own models; no dev cache."""
    monkeypatch.setattr(settings.env, "llm_provider", "openai")
    monkeypatch.setattr(settings.env, "extract_model", "gpt-extract")
    monkeypatch.setattr(settings.env, "reason_model", "gpt-reason")
    monkeypatch.setattr(settings.env, "llm_dev_cache", False)
    monkeypatch.setattr(settings.llm_fallback, "enabled", True)
    monkeypatch.setattr(settings.llm_fallback, "provider", "anthropic")
    monkeypatch.setattr(settings.llm_fallback, "extract_model", "claude-small")
    monkeypatch.setattr(settings.llm_fallback, "reason_model", "claude-big")
    llm.reset_fallback()
    yield
    llm.reset_fallback()


def _providers(monkeypatch, primary: RecordingProvider, fallback: RecordingProvider):
    monkeypatch.setattr(llm, "_provider", lambda name=None: {"openai": primary, "anthropic": fallback}[name or llm.active_provider()])


def test_primary_used_when_its_key_is_set(env, monkeypatch):
    monkeypatch.setattr(settings.env, "llm_api_key", SecretStr("sk-primary"))
    monkeypatch.setattr(settings.env, "anthropic_api_key", SecretStr("sk-ant"))
    primary, fallback = RecordingProvider("openai"), RecordingProvider("anthropic")
    _providers(monkeypatch, primary, fallback)
    obj, _ = llm.structured("p", Reply, "gpt-reason")
    assert obj.text == "ok from openai" and primary.models == ["gpt-reason"] and fallback.models == []
    assert llm.active_provider() == "openai"


def test_missing_primary_key_routes_to_fallback_models(env, monkeypatch, caplog):
    monkeypatch.setattr(settings.env, "llm_api_key", SecretStr(""))
    monkeypatch.setattr(settings.env, "anthropic_api_key", SecretStr("sk-ant"))
    primary, fallback = RecordingProvider("openai"), RecordingProvider("anthropic")
    _providers(monkeypatch, primary, fallback)
    with caplog.at_level("WARNING", logger="rfp_intel.llm"):
        llm.structured("p", Reply, "gpt-reason")
        llm.structured("p", Reply, "gpt-extract")
        llm.text("p", "something-else")
    assert primary.models == []
    assert fallback.models == ["claude-big", "claude-small", "claude-big"]
    assert llm.active_provider() == "anthropic"
    assert sum("LLM fallback" in r.message for r in caplog.records) == 1     # logged once
    assert "sk-ant" not in caplog.text


def test_rejected_key_switches_once_and_sticks(env, monkeypatch):
    monkeypatch.setattr(settings.env, "llm_api_key", SecretStr("sk-bad"))
    monkeypatch.setattr(settings.env, "anthropic_api_key", SecretStr("sk-ant"))
    primary = RecordingProvider("openai", fail=llm.LLMAuthError("OpenAI rejected the API key"))
    fallback = RecordingProvider("anthropic")
    _providers(monkeypatch, primary, fallback)
    obj, usage = llm.structured("p", Reply, "gpt-extract")
    assert obj.text == "ok from anthropic" and primary.models == ["gpt-extract"] and fallback.models == ["claude-small"]
    llm.structured("p", Reply, "gpt-extract")
    assert primary.models == ["gpt-extract"]                 # not retried on the primary afterwards
    assert fallback.models == ["claude-small", "claude-small"]


def test_no_fallback_key_means_the_error_propagates(env, monkeypatch):
    monkeypatch.setattr(settings.env, "llm_api_key", SecretStr("sk-bad"))
    monkeypatch.setattr(settings.env, "anthropic_api_key", SecretStr(""))
    primary = RecordingProvider("openai", fail=llm.LLMAuthError("rejected"))
    _providers(monkeypatch, primary, RecordingProvider("anthropic"))
    with pytest.raises(llm.LLMAuthError, match="rejected"):
        llm.structured("p", Reply, "gpt-reason")
    assert llm.active_provider() == "openai"


def test_fallback_disabled_keeps_primary(env, monkeypatch):
    monkeypatch.setattr(settings.llm_fallback, "enabled", False)
    monkeypatch.setattr(settings.env, "llm_api_key", SecretStr(""))
    monkeypatch.setattr(settings.env, "anthropic_api_key", SecretStr("sk-ant"))
    assert llm.active_provider() == "openai"
    with pytest.raises(llm.LLMAuthError, match="no API key for openai"):
        llm._provider()


def test_other_errors_do_not_trigger_fallback(env, monkeypatch):
    monkeypatch.setattr(settings.env, "llm_api_key", SecretStr("sk-primary"))
    monkeypatch.setattr(settings.env, "anthropic_api_key", SecretStr("sk-ant"))
    primary = RecordingProvider("openai", fail=llm.LLMError("OpenAI timeout after 60s"))
    fallback = RecordingProvider("anthropic")
    _providers(monkeypatch, primary, fallback)
    with pytest.raises(llm.LLMError, match="timeout"):
        llm.text("p", "gpt-reason")
    assert fallback.models == [] and llm.active_provider() == "openai"


def test_key_lookup_prefers_provider_specific_key_for_the_other_provider(env, monkeypatch):
    monkeypatch.setattr(settings.env, "llm_api_key", SecretStr("sk-primary"))
    monkeypatch.setattr(settings.env, "anthropic_api_key", SecretStr("sk-ant"))
    monkeypatch.setattr(settings.env, "openai_api_key", SecretStr("sk-openai-explicit"))
    assert llm._key_for("openai") == "sk-primary"            # LLM_API_KEY wins for the primary
    assert llm._key_for("anthropic") == "sk-ant"
    monkeypatch.setattr(settings.env, "llm_provider", "anthropic")
    assert llm._key_for("anthropic") == "sk-primary" and llm._key_for("openai") == "sk-openai-explicit"


def test_fallback_cost_uses_the_fallback_model_price(env, monkeypatch):
    monkeypatch.setattr(settings.env, "llm_api_key", SecretStr(""))
    monkeypatch.setattr(settings.env, "anthropic_api_key", SecretStr("sk-ant"))
    monkeypatch.setattr(settings.cost, "usd_per_1k_input", {"claude-big": 1.0})
    monkeypatch.setattr(settings.cost, "usd_per_1k_output", {"claude-big": 2.0})
    _providers(monkeypatch, RecordingProvider("openai"), RecordingProvider("anthropic"))
    _, usage = llm.structured("p", Reply, "gpt-reason")
    assert usage.cost_usd == pytest.approx(10 / 1000 * 1.0 + 5 / 1000 * 2.0)
