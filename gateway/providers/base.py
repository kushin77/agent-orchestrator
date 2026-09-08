"""Shared HTTP adapter base + OpenAI-compatible adapter (issue #15).

``HttpModelProvider`` implements the one-shot HTTP path of ``ModelProvider``:
build request -> transport round-trip (latency measured) -> parse response ->
validate typed output against the output schema (fail closed). Subclasses
supply the provider-specific pieces (endpoint, headers, payload, response
parsing) - see anthropic.py / deepseek.py / openai.py / gemini.py / ollama.py.

``Credentials`` carries an optional API key. The key is attached to the
outbound request headers only; it is never logged, never included in
exceptions/events, and never part of any stringified result.

``OpenAICompatProvider`` is the shared OpenAI ``chat/completions`` protocol
used by the ``openai`` (Copilot/GPT via any OpenAI-compatible endpoint) and
``deepseek`` adapters.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Mapping

from providers.config import ProviderConfig
from providers.contract import (
    SYSTEM,
    ChatMessage,
    ChatOptions,
    ChatResult,
    CallContext,
    ModelProvider,
    Usage,
)
from providers.errors import (
    ProviderConfigurationError,
    ProviderError,
    ProviderUnavailableError,
)
from providers.schema import parse_and_validate
from providers.transport import HttpTransport, HttpResponse


@dataclass(frozen=True)
class Credentials:
    """Bearer credential for one provider (never logged / stringified)."""

    api_key: str | None = None

    def __bool__(self) -> bool:
        return bool(self.api_key)


@dataclass(frozen=True)
class ParsedResponse:
    """Provider-neutral extraction of one raw provider response."""

    text: str
    usage: Usage
    model_used: str


def _status_message(status: int) -> str:
    return {
        400: "bad request",
        401: "authentication failed (check the API key)",
        403: "forbidden",
        404: "not found",
        409: "conflict",
        413: "payload too large",
        429: "rate limited",
    }.get(status, f"HTTP {status}")


class HttpModelProvider(ModelProvider):
    """Base for all injected-transport HTTP provider adapters."""

    #: Canonical provider key; concrete adapters override and must match
    #: the config.name they are constructed with.
    name = "abstract"

    def __init__(
        self,
        config: ProviderConfig,
        transport: HttpTransport,
        credentials: Credentials | None = None,
    ) -> None:
        if config.name != self.name:
            raise ProviderConfigurationError(
                f"provider config name {config.name!r} does not match adapter "
                f"{self.name!r}"
            )
        self._config = config
        self._transport = transport
        self._credentials = credentials or Credentials()

    # -- ModelProvider ------------------------------------------------------ #
    def chat(
        self,
        messages: list[ChatMessage],
        schema: Mapping[str, Any] | None,
        options: ChatOptions,
        context: CallContext,
    ) -> ChatResult:
        model = options.model or self._config.default_model
        if not self._config.model_supported(model):
            raise ProviderConfigurationError(
                f"model {model!r} is not supported by provider {self.name}",
                provider=self.name,
                model=model,
            )
        url = self.endpoint_url(model)
        headers = self.build_headers()
        payload = self.build_payload(messages, model, options)
        timeout_ms = options.timeout_ms or self._config.timeout_ms
        started = time.monotonic()
        response = self._transport.request(
            "POST", url, headers=headers, body=payload, timeout_ms=timeout_ms
        )
        latency_ms = (time.monotonic() - started) * 1000.0
        if not 200 <= response.status < 300:
            self._raise_http_error(response, model)
        parsed = self.parse_response(response, model)
        content = parse_and_validate(parsed.text, schema)
        return ChatResult(
            provider=self.name,
            model=parsed.model_used,
            content=content,
            usage=parsed.usage,
            latency_ms=latency_ms,
            tenant_id=context.tenant_id,
            agent_id=context.agent_id,
            logical_key=context.logical_key,
            raw_text=parsed.text if schema is not None else None,
        )

    def _raise_http_error(self, response: HttpResponse, model: str) -> None:
        if response.status >= 500 or response.status == 429:
            raise ProviderUnavailableError(
                f"{self.name} returned {_status_message(response.status)}",
                provider=self.name,
                model=model,
            )
        raise ProviderError(
            f"{self.name} returned {_status_message(response.status)}",
            provider=self.name,
            model=model,
        )

    # -- subclass hooks ----------------------------------------------------- #
    def endpoint_url(self, model: str) -> str:
        return f"{self._config.base_url.rstrip('/')}{self._config.api_path}"

    def build_headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        auth = self._auth_header()
        if auth:
            headers.update(auth)
        headers.update(self._config.extra_headers)
        return headers

    def _auth_header(self) -> dict[str, str]:
        if self._credentials.api_key:
            return {"authorization": f"Bearer {self._credentials.api_key}"}
        return {}

    def build_payload(
        self,
        messages: list[ChatMessage],
        model: str,
        options: ChatOptions,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def parse_response(
        self,
        response: HttpResponse,
        requested_model: str,
    ) -> ParsedResponse:
        raise NotImplementedError


class OpenAICompatProvider(HttpModelProvider):
    """Shared OpenAI ``/chat/completions`` protocol (openai + deepseek).

    Both OpenAI (including Copilot/GPT deployments reachable through an
    OpenAI-compatible base URL) and DeepSeek expose this wire shape, so the
    request builder and response parser live here once.
    """

    def build_payload(
        self,
        messages: list[ChatMessage],
        model: str,
        options: ChatOptions,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in messages
            ],
        }
        if options.temperature is not None:
            payload["temperature"] = options.temperature
        if options.max_tokens is not None:
            payload["max_tokens"] = options.max_tokens
        return payload

    def parse_response(
        self,
        response: HttpResponse,
        requested_model: str,
    ) -> ParsedResponse:
        body = _require_json_object(response)
        choices = body.get("choices") or []
        if not choices:
            raise ProviderError(
                f"{self.name} response contained no choices",
                provider=self.name,
                model=requested_model,
            )
        text = (choices[0].get("message") or {}).get("content") or ""
        usage_raw = body.get("usage") or {}
        usage = Usage(
            input_tokens=int(usage_raw.get("prompt_tokens") or 0),
            output_tokens=int(usage_raw.get("completion_tokens") or 0),
        )
        model_used = body.get("model") or requested_model
        return ParsedResponse(text=text, usage=usage, model_used=model_used)


def _require_json_object(response: HttpResponse) -> dict[str, Any]:
    """Parse a provider response body as a JSON object (raises on failure)."""
    try:
        body = json.loads(response.body)
    except json.JSONDecodeError as exc:
        raise ProviderError("provider returned a non-JSON response body") from exc
    if not isinstance(body, dict):
        raise ProviderError("provider response was not a JSON object")
    return body


def _system_text(messages: list[ChatMessage]) -> str | None:
    """Join all system messages into one system prompt string (or None)."""
    joined = "\n\n".join(m.content for m in messages if m.role == SYSTEM)
    return joined or None


def _chat_roles(messages: list[ChatMessage]) -> list[ChatMessage]:
    """Return messages with the system role excluded (provider separation)."""
    return [m for m in messages if m.role != SYSTEM]
