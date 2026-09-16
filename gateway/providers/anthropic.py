"""Anthropic Claude adapter (issue #15) - Messages API over the injected transport.

Wire protocol (``POST {base}/v1/messages``):

- ``x-api-key`` auth + ``anthropic-version`` header,
- system messages lifted out of the message list into the top-level
  ``system`` field,
- response ``content`` blocks (type ``text``) concatenated; usage maps
  ``input_tokens`` / ``output_tokens``.

Model tiers mirror the harvested gmail-agent Claude client
(sonnet/opus/haiku) mapped onto the issue-#9 tiers
(``LOW`` -> haiku, ``MED``/``HIGH`` -> sonnet, ``MAX`` -> opus).

Claude-specific capabilities (claude-anthropic module.json ``features``),
both flag-gated OFF by default (GR-28) via ``ProviderConfig.provider_options``
- a config with an empty/absent bag behaves exactly as before:

- ``prompt_caching`` (bool) - marks the system prompt and the trailing
  message content block ``cache_control: {"type": "ephemeral"}`` so a stable
  prefix (tools -> system -> messages) is eligible for Anthropic's prompt
  cache. See ``docs/CROSS-REPO-DEEPSEEK-ENHANCEMENTS.md`` pattern + the
  Claude API skill's prompt-caching reference.
- ``thinking_effort`` (``"low"``/``"medium"``/``"high"``/``"xhigh"``/``"max"``)
  - enables adaptive thinking (``thinking: {"type": "adaptive"}``) at the
  given ``output_config.effort``. Current Claude models (Fable 5.1, Opus 5,
  Sonnet 5) reject the deprecated ``budget_tokens`` shape; this adapter never
  sends it.
"""

from __future__ import annotations

from typing import Any, Mapping

from providers.base import (
    HttpModelProvider,
    ParsedResponse,
    _chat_roles,
    _require_json_object,
    _system_text,
)
from providers.config import ProviderConfig
from providers.contract import ChatMessage, ChatOptions, Usage
from providers.errors import ProviderError
from providers.transport import HttpTransport, HttpResponse


class AnthropicProvider(HttpModelProvider):
    """Claude adapter speaking the Anthropic Messages API."""

    name = "anthropic"

    def __init__(
        self,
        config: ProviderConfig,
        transport: HttpTransport,
        credentials=None,
    ) -> None:
        super().__init__(config, transport, credentials)

    def build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        if self._credentials.api_key:
            headers["x-api-key"] = self._credentials.api_key
        headers.update(self._config.extra_headers)
        return headers

    def build_payload(
        self,
        messages: list[ChatMessage],
        model: str,
        options: ChatOptions,
    ) -> dict[str, Any]:
        caching = bool(self._config.provider_options.get("prompt_caching"))
        chat_messages = [
            {"role": m.role, "content": m.content} for m in _chat_roles(messages)
        ]
        if caching and chat_messages:
            # Cache the stable prefix: everything up to and including the
            # last message becomes eligible once marked. Only the trailing
            # block needs the breakpoint (Anthropic caches the whole prefix
            # up to it); render order is tools -> system -> messages.
            last = chat_messages[-1]
            last["content"] = [
                {
                    "type": "text",
                    "text": last["content"],
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": options.max_tokens or 1024,
            "messages": chat_messages,
        }
        system = _system_text(messages)
        if system is not None:
            if caching:
                payload["system"] = [
                    {
                        "type": "text",
                        "text": system,
                        "cache_control": {"type": "ephemeral"},
                    }
                ]
            else:
                payload["system"] = system
        if options.temperature is not None:
            payload["temperature"] = options.temperature
        thinking_effort = self._config.provider_options.get("thinking_effort")
        if thinking_effort:
            payload["thinking"] = {"type": "adaptive"}
            payload["output_config"] = {"effort": thinking_effort}
        return payload

    def parse_response(
        self,
        response: HttpResponse,
        requested_model: str,
    ) -> ParsedResponse:
        body = _require_json_object(response)
        blocks = body.get("content") or []
        text = "".join(
            block.get("text", "")
            for block in blocks
            if isinstance(block, Mapping) and block.get("type") == "text"
        )
        usage_raw = body.get("usage") or {}
        usage = Usage(
            input_tokens=int(usage_raw.get("input_tokens") or 0),
            output_tokens=int(usage_raw.get("output_tokens") or 0),
        )
        model_used = body.get("model") or requested_model
        if not text and not body.get("stop_reason"):
            raise ProviderError(
                "anthropic response contained no text content",
                provider=self.name,
                model=requested_model,
            )
        return ParsedResponse(text=text, usage=usage, model_used=model_used)
