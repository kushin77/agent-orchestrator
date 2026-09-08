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
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": options.max_tokens or 1024,
            "messages": [
                {"role": m.role, "content": m.content}
                for m in _chat_roles(messages)
            ],
        }
        system = _system_text(messages)
        if system is not None:
            payload["system"] = system
        if options.temperature is not None:
            payload["temperature"] = options.temperature
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
