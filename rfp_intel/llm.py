"""Provider-agnostic LLM wrapper (BUILD_PLAN 6.3). Every LLM call goes through here.

- `structured(prompt, model_cls, model, system)` -> (validated model, Usage)
- `text(prompt, model, system)` -> (str, Usage)

Provider comes from LLM_PROVIDER (openai | anthropic); models from
EXTRACT_MODEL / REASON_MODEL. When that provider has no key (LLM_API_KEY
unset) or rejects it (401/403), calls go to the `llm_fallback` provider
from settings.yaml with its own models, as long as its key is in .env
(ANTHROPIC_API_KEY / OPENAI_API_KEY). The switch is logged once and sticks
for the process. Structured output uses the provider's JSON-schema mode. Invalid output is retried `llm.max_json_retries` times with
the validation error appended; after that, and on timeouts or API errors,
`LLMError` is raised.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, SecretStr, ValidationError

from rfp_intel.config import settings
from rfp_intel.logging_utils import raise_if_cancelled, record_llm_call
from rfp_intel.schemas import Usage

T = TypeVar("T", bound=BaseModel)
logger = logging.getLogger("rfp_intel.llm")

# JSON-schema keywords that strict structured-output modes reject; Pydantic
# still enforces them when the response is validated.
_UNSUPPORTED_KEYWORDS = {
    "default", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "minLength", "maxLength", "pattern", "format", "minItems", "maxItems",
}

# Models that rejected a `temperature` value; it is omitted for them afterwards.
_NO_TEMPERATURE: set[str] = set()

# OpenAI-compatible providers (Chat Completions API at a different base URL).
# They use JSON-object mode with the schema in the system prompt, because
# strict `json_schema` support varies between them; Pydantic still validates.
# LLM_BASE_URL overrides the preset URL (or names one for provider "compatible").
COMPATIBLE_BASE_URLS: dict[str, str] = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": "http://localhost:11434/v1",
    "compatible": "",
}

# Sticky provider fallback state for this process.
_fallback_active = False
_fallback_lock = threading.Lock()


class LLMError(Exception):
    """An LLM call failed: timeout, API error, refusal or invalid output."""


class LLMAuthError(LLMError):
    """The provider rejected the API key (missing, invalid or not permitted)."""


class LLMTruncated(LLMError):
    """The reply stopped at `llm.max_tokens` (reasoning models count hidden reasoning tokens against it)."""


@dataclass
class RawResponse:
    """What a provider returns before validation."""

    text: str
    tokens_in: int
    tokens_out: int
    truncated: bool = False              # the provider stopped at the output token limit


class Provider(Protocol):
    """Minimal interface each LLM provider implements."""

    def complete(
        self, prompt: str, system: str | None, model: str, schema: dict | None, schema_name: str
    ) -> RawResponse:
        """Run one completion; `schema` requests JSON output matching it."""
        ...


# ---------------------------------------------------------------- public API


def structured(
    prompt: str, model_cls: type[T], model: str, system: str | None = None
) -> tuple[T, Usage]:
    """Call the LLM and return a validated `model_cls` instance plus usage."""
    schema, is_strict = strict_json_schema(model_cls)
    if active_provider() in COMPATIBLE_BASE_URLS:   # no reliable strict mode: schema goes in the prompt
        schema, is_strict = copy.deepcopy(model_cls.model_json_schema()), False
    system_text = system if is_strict else _with_schema_hint(system, schema)
    attempt_prompt, total = prompt, Usage()
    last_error = ""
    for _ in range(settings.llm.max_json_retries + 1):
        try:
            raw, usage = _call(attempt_prompt, system_text, model, schema if is_strict else None,
                               model_cls.__name__, json_mode=not is_strict)
        except LLMTruncated as exc:        # nothing to correct: retry the same prompt once more
            last_error, attempt_prompt = str(exc), prompt
            continue
        total = total + usage
        try:
            return model_cls.model_validate_json(_strip_fences(raw)), total
        except ValidationError as exc:
            last_error = str(exc)[:2000]
            attempt_prompt = (
                f"{prompt}\n\nYour previous reply was not valid for the required schema:\n"
                f"{last_error}\nReply again with JSON that matches the schema exactly."
            )
    raise LLMError(f"invalid structured output for {model_cls.__name__}: {last_error}")


def text(prompt: str, model: str, system: str | None = None) -> tuple[str, Usage]:
    """Call the LLM for free text."""
    return _call(prompt, system, model, None, "text")


def strict_json_schema(model_cls: type[BaseModel]) -> tuple[dict, bool]:
    """JSON schema for strict structured output, and whether strict mode is possible.

    Strict mode needs every object to list all properties as required and to
    forbid extra keys. Free-form dicts (`dict[str, X]`) cannot be expressed
    that way, so such models fall back to non-strict JSON mode.
    """
    schema = copy.deepcopy(model_cls.model_json_schema())
    is_strict = _make_strict(schema)
    return schema, is_strict


# ---------------------------------------------------------------- provider routing


def active_provider() -> str:
    """Name of the provider calls go to right now (primary, or the fallback once it kicked in)."""
    return _route()[0]


def resolve_model(model: str) -> tuple[str, str]:
    """(provider, model actually called) for a configured model name.

    On the fallback provider REASON_MODEL maps to `llm_fallback.reason_model`
    and EXTRACT_MODEL to `llm_fallback.extract_model`; any other name gets the
    reason model.
    """
    name, using_fallback = _route()
    if not using_fallback:
        return name, model
    fallback, env = settings.llm_fallback, settings.env
    if model == env.reason_model:
        return name, fallback.reason_model or model
    if model == env.extract_model:
        return name, fallback.extract_model or model
    return name, fallback.reason_model or model


def reset_fallback() -> None:
    """Forget a fallback switch (tests)."""
    global _fallback_active
    with _fallback_lock:
        _fallback_active = False


def _key_for(provider: str) -> str:
    """API key of a provider: LLM_API_KEY for the primary one, else <PROVIDER>_API_KEY."""
    env = settings.env
    if provider == env.llm_provider and env.llm_api_key.get_secret_value():
        return env.llm_api_key.get_secret_value()
    keys: dict[str, SecretStr] = {"openai": env.openai_api_key, "anthropic": env.anthropic_api_key}
    return keys.get(provider, SecretStr("")).get_secret_value()


def _fallback_available() -> bool:
    """A fallback exists: enabled, a different provider than the primary, and its key is set."""
    fallback = settings.llm_fallback
    return fallback.enabled and fallback.provider != settings.env.llm_provider and bool(_key_for(fallback.provider))


def _route() -> tuple[str, bool]:
    """(provider name, whether that is the fallback)."""
    primary = settings.env.llm_provider
    if _fallback_active:
        return settings.llm_fallback.provider, True
    if not _key_for(primary) and _fallback_available():
        _activate_fallback(f"no API key for {primary}")
        return settings.llm_fallback.provider, True
    return primary, False


def _activate_fallback(reason: str) -> None:
    """Switch this process to the fallback provider (logged once; the key itself is never logged)."""
    global _fallback_active
    with _fallback_lock:
        if _fallback_active:
            return
        _fallback_active = True
    fallback = settings.llm_fallback
    logger.warning("LLM fallback: %s; using %s (%s / %s) for the rest of this process",
                   reason, fallback.provider, fallback.extract_model, fallback.reason_model)


def compute_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """USD cost from the `cost` table in settings.yaml (0 for unknown models)."""
    cost = settings.cost
    return (tokens_in / 1000) * cost.usd_per_1k_input.get(model, 0.0) + (
        tokens_out / 1000
    ) * cost.usd_per_1k_output.get(model, 0.0)


# ---------------------------------------------------------------- internals


def _call(
    prompt: str, system: str | None, model: str, schema: dict | None, schema_name: str,
    json_mode: bool = False,
) -> tuple[str, Usage]:
    """One provider call with dev cache, usage accounting and trace recording.

    `json_mode` asks OpenAI-compatible providers for a JSON object when no strict
    schema is sent (other providers ignore it; the schema hint is in `system`).
    """
    raise_if_cancelled()  # a stopped run must not start another provider request
    if not model:
        raise LLMError("no model configured (set EXTRACT_MODEL / REASON_MODEL in .env)")
    provider_name, actual_model = resolve_model(model)
    if json_mode and schema is None and provider_name in COMPATIBLE_BASE_URLS:
        schema_name = f"json:{schema_name}"   # marker read by CompatibleProvider
    cache_file = _cache_path(prompt, system, actual_model, schema_name)
    if cache_file is not None and cache_file.exists():
        record_llm_call(actual_model, Usage(), 0, schema=schema_name, cached=True, provider=provider_name)
        return json.loads(cache_file.read_text(encoding="utf-8"))["text"], Usage()

    t0 = time.perf_counter()
    try:
        raw = _provider(provider_name).complete(prompt, system, actual_model, schema, schema_name)
    except LLMAuthError as exc:
        if _fallback_active or not _fallback_available():
            raise
        _activate_fallback(f"{provider_name} rejected the API key ({exc})")
        provider_name, actual_model = resolve_model(model)
        raw = _provider(provider_name).complete(prompt, system, actual_model, schema, schema_name)
    latency_ms = int((time.perf_counter() - t0) * 1000)
    usage = Usage(
        tokens_in=raw.tokens_in,
        tokens_out=raw.tokens_out,
        cost_usd=compute_cost(actual_model, raw.tokens_in, raw.tokens_out),
    )
    record_llm_call(actual_model, usage, latency_ms, schema=schema_name, cached=False, provider=provider_name)
    if cache_file is not None:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({"text": raw.text}), encoding="utf-8")
    if raw.truncated:
        raise LLMTruncated(f"reply truncated at llm.max_tokens={settings.llm.max_tokens} "
                           f"({raw.tokens_out} output tokens incl. reasoning); raise llm.max_tokens in config/settings.yaml")
    return raw.text, usage


def _cache_path(prompt: str, system: str | None, model: str, schema_name: str) -> Path | None:
    """Dev-cache file for this request, or None when LLM_DEV_CACHE is off."""
    if not settings.env.llm_dev_cache:
        return None
    key = hashlib.sha256(f"{model}\n{schema_name}\n{system or ''}\n{prompt}".encode()).hexdigest()
    return settings.path("storage_dir") / "llm_cache" / f"{key}.json"


def _provider(name: str | None = None) -> Provider:
    """The provider client for `name` (default: the routed provider)."""
    name = name or active_provider()
    api_key = _key_for(name)
    if not api_key:
        raise LLMAuthError(f"no API key for {name} (set LLM_API_KEY, or {name.upper()}_API_KEY for the fallback)")
    if name == "openai":
        return OpenAIProvider(api_key)
    if name == "anthropic":
        return AnthropicProvider(api_key)
    if name in COMPATIBLE_BASE_URLS:
        base_url = settings.env.llm_base_url or COMPATIBLE_BASE_URLS[name]
        if not base_url:
            raise LLMError("LLM_PROVIDER=compatible needs LLM_BASE_URL")
        return CompatibleProvider(api_key, base_url)
    known = ", ".join(["openai", "anthropic", *COMPATIBLE_BASE_URLS])
    raise LLMError(f"unknown LLM_PROVIDER {name!r} (use one of: {known})")


def _make_strict(node: Any) -> bool:
    """Rewrite a schema in place for strict mode; False if it cannot be strict."""
    ok = True
    if isinstance(node, dict):
        for key in _UNSUPPORTED_KEYWORDS & node.keys():
            del node[key]
        if node.get("type") == "object" or "properties" in node:
            if "properties" not in node or isinstance(node.get("additionalProperties"), dict):
                ok = False  # dict / dict[str, X]: open-ended keys
            else:
                node["required"] = list(node["properties"])
                node["additionalProperties"] = False
        for key, child in node.items():
            if key in {"properties", "$defs"} and isinstance(child, dict):
                ok = all([_make_strict(sub) for sub in child.values()]) and ok
            elif isinstance(child, (dict, list)):
                ok = _make_strict(child) and ok
    elif isinstance(node, list):
        ok = all([_make_strict(item) for item in node])
    return ok


def _with_schema_hint(system: str | None, schema: dict) -> str:
    """System prompt that asks for JSON matching `schema` (non-strict mode)."""
    hint = "Reply with only a JSON object matching this JSON schema:\n" + json.dumps(schema)
    return f"{system}\n\n{hint}" if system else hint


def _strip_fences(raw: str) -> str:
    """Remove ``` fences a model may wrap around JSON in non-strict mode."""
    stripped = raw.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        stripped = stripped.rsplit("```", 1)[0]
    return stripped.strip()


def _temperature_rejected(exc: Exception) -> bool:
    """True if an API error says the model does not accept `temperature`."""
    return getattr(exc, "param", None) == "temperature" or "temperature" in str(exc).lower()


# ---------------------------------------------------------------- providers


class OpenAIProvider:
    """OpenAI Chat Completions with strict JSON-schema response format."""

    def __init__(self, api_key: str) -> None:
        import openai

        self._openai = openai
        self._client = openai.OpenAI(api_key=api_key, timeout=settings.llm.timeout_s, max_retries=2)

    def complete(
        self, prompt: str, system: str | None, model: str, schema: dict | None, schema_name: str
    ) -> RawResponse:
        """Run one chat completion."""
        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}
        ]
        params: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_completion_tokens": settings.llm.max_tokens,
        }
        if schema is not None:
            params["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            }
        response = self._create_with_temperature_fallback(params)
        choice = response.choices[0]
        if getattr(choice.message, "refusal", None):
            raise LLMError(f"model refused: {choice.message.refusal}")
        usage = response.usage
        return RawResponse(
            text=choice.message.content or "",
            tokens_in=usage.prompt_tokens if usage else 0,
            tokens_out=usage.completion_tokens if usage else 0,
            truncated=getattr(choice, "finish_reason", None) == "length",
        )

    def _create_with_temperature_fallback(self, params: dict[str, Any]) -> Any:
        """Send with the configured temperature; drop it if the model rejects it."""
        model = params["model"]
        if model not in _NO_TEMPERATURE:
            try:
                return self._send({**params, "temperature": settings.llm.temperature})
            except self._openai.BadRequestError as exc:
                if not _temperature_rejected(exc):
                    raise LLMError(f"OpenAI bad request: {exc}") from exc
                _NO_TEMPERATURE.add(model)
        try:
            return self._send(params)
        except self._openai.BadRequestError as exc:
            raise LLMError(f"OpenAI bad request: {exc}") from exc

    def _send(self, params: dict[str, Any]) -> Any:
        """One API request with SDK errors mapped to LLMError (except 400s)."""
        try:
            return self._client.chat.completions.create(**params)
        except self._openai.APITimeoutError as exc:
            raise LLMError(f"OpenAI timeout after {settings.llm.timeout_s}s") from exc
        except self._openai.BadRequestError:
            raise
        except (self._openai.AuthenticationError, self._openai.PermissionDeniedError) as exc:
            raise LLMAuthError(f"OpenAI rejected the API key: {type(exc).__name__}") from exc
        except self._openai.APIError as exc:
            raise LLMError(f"OpenAI API error: {type(exc).__name__}: {exc}") from exc


class CompatibleProvider(OpenAIProvider):
    """OpenAI-compatible Chat Completions endpoint (Gemini, Groq, OpenRouter, Ollama, ...).

    Uses `max_tokens` (widely supported) instead of `max_completion_tokens`, and
    JSON-object mode for structured calls; more SDK retries absorb free-tier 429s.
    """

    def __init__(self, api_key: str, base_url: str) -> None:
        import openai

        self._openai = openai
        self._client = openai.OpenAI(
            api_key=api_key, base_url=base_url, timeout=settings.llm.timeout_s, max_retries=5
        )

    def complete(
        self, prompt: str, system: str | None, model: str, schema: dict | None, schema_name: str
    ) -> RawResponse:
        """Run one chat completion (JSON-object mode when `schema_name` has the json: marker)."""
        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}
        ]
        params: dict[str, Any] = {"model": model, "messages": messages,
                                  "max_tokens": settings.llm.max_tokens}
        if schema is not None:
            params["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            }
        elif schema_name.startswith("json:"):
            params["response_format"] = {"type": "json_object"}
        response = self._create_with_temperature_fallback(params)
        if not response.choices:
            raise LLMError("OpenAI-compatible provider returned no choices")
        choice = response.choices[0]
        usage = response.usage
        return RawResponse(
            text=choice.message.content or "",
            tokens_in=getattr(usage, "prompt_tokens", 0) or 0 if usage else 0,
            tokens_out=getattr(usage, "completion_tokens", 0) or 0 if usage else 0,
            truncated=getattr(choice, "finish_reason", None) == "length",
        )


class AnthropicProvider:
    """Anthropic Messages API with `output_config.format` JSON schema."""

    def __init__(self, api_key: str) -> None:
        import anthropic

        self._anthropic = anthropic
        self._client = anthropic.Anthropic(
            api_key=api_key, timeout=settings.llm.timeout_s, max_retries=2
        )

    def complete(
        self, prompt: str, system: str | None, model: str, schema: dict | None, schema_name: str
    ) -> RawResponse:
        """Run one Messages API call."""
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": settings.llm.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            params["system"] = system
        if schema is not None:
            params["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
        response = self._create_with_temperature_fallback(params)
        if response.stop_reason == "refusal":
            raise LLMError("model refused the request")
        text_out = "".join(block.text for block in response.content if block.type == "text")
        return RawResponse(
            text=text_out,
            tokens_in=response.usage.input_tokens,
            tokens_out=response.usage.output_tokens,
            truncated=response.stop_reason == "max_tokens",
        )

    def _create_with_temperature_fallback(self, params: dict[str, Any]) -> Any:
        """Send with the configured temperature; drop it if the model rejects it."""
        model = params["model"]
        if model not in _NO_TEMPERATURE:
            try:
                return self._send({**params, "temperature": settings.llm.temperature})
            except self._anthropic.BadRequestError as exc:
                if not _temperature_rejected(exc):
                    raise LLMError(f"Anthropic bad request: {exc}") from exc
                _NO_TEMPERATURE.add(model)
        try:
            return self._send(params)
        except self._anthropic.BadRequestError as exc:
            raise LLMError(f"Anthropic bad request: {exc}") from exc

    def _send(self, params: dict[str, Any]) -> Any:
        """One API request with SDK errors mapped to LLMError (except 400s)."""
        try:
            return self._client.messages.create(**params)
        except self._anthropic.APITimeoutError as exc:
            raise LLMError(f"Anthropic timeout after {settings.llm.timeout_s}s") from exc
        except self._anthropic.BadRequestError:
            raise
        except (self._anthropic.AuthenticationError, self._anthropic.PermissionDeniedError) as exc:
            raise LLMAuthError(f"Anthropic rejected the API key: {type(exc).__name__}") from exc
        except self._anthropic.APIError as exc:
            raise LLMError(f"Anthropic API error: {type(exc).__name__}: {exc}") from exc
