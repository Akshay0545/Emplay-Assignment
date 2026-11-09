"""Tests for rfp_intel.llm with a fake provider (no network)."""

import httpx
import openai
import pytest
from pydantic import BaseModel

from rfp_intel import llm
from rfp_intel.config import settings
from rfp_intel.schemas import Answer, FieldExtraction, Plan, ValidationResult


class DueDate(BaseModel):
    date: str
    time_zone: str | None = None


TRUNCATED = object()      # a reply that stopped at the output token limit


class FakeProvider:
    """Returns queued replies and records the prompts it was sent."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts: list[str] = []
        self.schemas: list[dict | None] = []

    def complete(self, prompt, system, model, schema, schema_name):
        self.prompts.append(prompt)
        self.schemas.append(schema)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if reply is TRUNCATED:
            return llm.RawResponse(text="", tokens_in=100, tokens_out=4000, truncated=True)
        return llm.RawResponse(text=reply, tokens_in=100, tokens_out=10)


@pytest.fixture
def fake(monkeypatch):
    def install(*replies):
        provider = FakeProvider(replies)
        monkeypatch.setattr(llm, "_provider", lambda name=None: provider)
        return provider

    monkeypatch.setattr(settings.env, "llm_dev_cache", False)
    return install


def test_structured_returns_model_and_usage(fake, monkeypatch):
    monkeypatch.setattr(settings.cost, "usd_per_1k_input", {"m": 1.0})
    monkeypatch.setattr(settings.cost, "usd_per_1k_output", {"m": 2.0})
    fake('{"date": "2024-07-09", "time_zone": "CDT"}')
    obj, usage = llm.structured("p", DueDate, "m")
    assert obj == DueDate(date="2024-07-09", time_zone="CDT")
    assert (usage.tokens_in, usage.tokens_out) == (100, 10)
    assert usage.cost_usd == pytest.approx(0.1 + 0.02)


def test_structured_retries_once_with_validation_error(fake):
    provider = fake('{"wrong": 1}', '{"date": "2024-07-09", "time_zone": null}')
    obj, usage = llm.structured("original prompt", DueDate, "m")
    assert obj.date == "2024-07-09"
    assert len(provider.prompts) == 2
    assert provider.prompts[1].startswith("original prompt")
    assert "not valid" in provider.prompts[1] and "date" in provider.prompts[1]
    assert usage.tokens_in == 200


def test_structured_raises_llm_error_after_retry(fake):
    fake("not json", "still not json")
    with pytest.raises(llm.LLMError, match="invalid structured output"):
        llm.structured("p", DueDate, "m")


def test_truncated_reply_is_retried_with_the_same_prompt(fake):
    provider = fake(TRUNCATED, '{"date": "2024-07-09", "time_zone": null}')
    obj, _ = llm.structured("original prompt", DueDate, "m")
    assert obj.date == "2024-07-09"
    assert provider.prompts == ["original prompt", "original prompt"]   # no "not valid" hint appended


def test_truncated_twice_names_the_token_budget(fake):
    fake(TRUNCATED, TRUNCATED)
    with pytest.raises(llm.LLMError, match=r"truncated at llm\.max_tokens=\d+"):
        llm.structured("p", DueDate, "m")


def test_text_call_reports_truncation(fake):
    fake(TRUNCATED)
    with pytest.raises(llm.LLMTruncated):
        llm.text("p", "m")


def test_provider_error_propagates_as_llm_error(fake):
    fake(llm.LLMError("timeout"))
    with pytest.raises(llm.LLMError, match="timeout"):
        llm.structured("p", DueDate, "m")


def test_missing_model_raises_llm_error(fake):
    fake()
    with pytest.raises(llm.LLMError, match="no model configured"):
        llm.text("p", "")


def test_unknown_model_costs_zero():
    assert llm.compute_cost("unknown-model", 1000, 1000) == 0.0


def test_dev_cache_skips_second_call(fake, monkeypatch, tmp_path):
    monkeypatch.setattr(settings.paths, "storage_dir", str(tmp_path))
    monkeypatch.setattr(settings.env, "llm_dev_cache", True)
    provider = fake("hello")
    first = llm.text("same prompt", "m")
    second = llm.text("same prompt", "m")
    assert first[0] == second[0] == "hello"
    assert len(provider.prompts) == 1
    assert second[1].tokens_in == 0


def _walk(node):
    if isinstance(node, dict):
        yield node
        for child in node.values():
            yield from _walk(child)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


@pytest.mark.parametrize("model_cls", [FieldExtraction, Plan, Answer, DueDate])
def test_strict_schema_for_llm_output_models(model_cls):
    schema, is_strict = llm.strict_json_schema(model_cls)
    assert is_strict
    for node in _walk(schema):
        assert "default" not in node
        if "properties" in node:
            assert set(node["required"]) == set(node["properties"])
            assert node["additionalProperties"] is False


def test_open_dict_models_fall_back_to_non_strict(fake):
    _, is_strict = llm.strict_json_schema(ValidationResult)
    assert not is_strict
    provider = fake('{"field": "f", "passed": true, "checks": {"has_citation": true}}')
    obj, _ = llm.structured("p", ValidationResult, "m")
    assert obj.checks == {"has_citation": True}
    assert provider.schemas == [None]


def test_strip_fences():
    assert llm._strip_fences('```json\n{"a": 1}\n```') == '{"a": 1}'


def _bad_request(param: str) -> openai.BadRequestError:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    return openai.BadRequestError(
        f"Unsupported value: '{param}'",
        response=httpx.Response(400, request=request),
        body={"param": param},
    )


class _FakeCompletions:
    def __init__(self, errors):
        self.errors = list(errors)
        self.calls: list[dict] = []

    def create(self, **params):
        self.calls.append(params)
        if self.errors:
            raise self.errors.pop(0)
        message = type("M", (), {"content": '{"date": "x"}', "refusal": None})
        usage = type("U", (), {"prompt_tokens": 5, "completion_tokens": 2})
        return type("R", (), {"choices": [type("C", (), {"message": message})], "usage": usage})


def _openai_provider(errors):
    provider = llm.OpenAIProvider.__new__(llm.OpenAIProvider)
    provider._openai = openai
    completions = _FakeCompletions(errors)
    provider._client = type("Client", (), {"chat": type("Chat", (), {"completions": completions})})
    return provider, completions


def test_openai_drops_rejected_temperature(monkeypatch):
    monkeypatch.setattr(llm, "_NO_TEMPERATURE", set())
    provider, completions = _openai_provider([_bad_request("temperature")])
    raw = provider.complete("p", None, "reasoning-model", None, "text")
    assert raw.text == '{"date": "x"}'
    assert "temperature" in completions.calls[0]
    assert "temperature" not in completions.calls[1]
    provider.complete("p", None, "reasoning-model", None, "text")
    assert "temperature" not in completions.calls[2]


def test_openai_other_bad_request_is_llm_error(monkeypatch):
    monkeypatch.setattr(llm, "_NO_TEMPERATURE", set())
    provider, _ = _openai_provider([_bad_request("messages")])
    with pytest.raises(llm.LLMError, match="bad request"):
        provider.complete("p", None, "m", None, "text")
