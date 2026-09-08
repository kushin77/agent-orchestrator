"""Google Gemini adapter (issue #15) - ``:generateContent`` protocol.

Wire protocol (``POST {base}/models/{model}:generateContent``):

- system prompt goes into ``systemInstruction`` (Gemini has no ``system``
  message role); assistant messages map to role ``model``,
- ``generationConfig`` carries sampling parameters,
- response usage maps ``promptTokenCount`` / ``candidatesTokenCount`` and the
  model id comes from ``modelVersion``.

Auth is the ``x-goog-api-key`` header (Generative Language API); a Vertex AI
deployment can be reached by overriding ``base_url`` + ``extra_headers`` in
``ProviderConfig`` (the capital-underwriting harvested client uses the same
per-deployment override approach).
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
from providers.contract import ASSISTANT, ChatMessage, ChatOptions, Usage
from providers.errors import ProviderError
from providers.transport import HttpTransport, HttpResponse


class GeminiProvider(HttpModelProvider):
    """Gemini adapter speaking the ``generateContent`` protocol."""

    name = "gemini"

    def __init__(
        self,
        config: ProviderConfig,
        transport: HttpTransport,
        credentials=None,
    ) -> None:
        super().__init__(config, transport, credentials)

    def endpoint_url(self, model: str) -> str:
        # The model id is part of the Gemini resource path.
        return f"{self._config.base_url.rstrip('/')}/models/{model}:generateContent"

    def build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"content-type": "application/json"}
        if self._credentials.api_key:
            headers["x-goog-api-key"] = self._credentials.api_key
        headers.update(self._config.extra_headers)
        return headers

    def build_payload(
        self,
        messages: list[ChatMessage],
        model: str,
        options: ChatOptions,
    ) -> dict[str, Any]:
        contents = []
        for message in _chat_roles(messages):
            role = "model" if message.role == ASSISTANT else message.role
            contents.append({"role": role, "parts": [{"text": message.content}]})
        payload: dict[str, Any] = {"contents": contents}
        system = _system_text(messages)
        if system is not None:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        generation: dict[str, Any] = {}
        if options.temperature is not None:
            generation["temperature"] = options.temperature
        if options.max_tokens is not None:
            generation["maxOutputTokens"] = options.max_tokens
        if generation:
            payload["generationConfig"] = generation
        return payload

    def parse_response(
        self,
        response: HttpResponse,
        requested_model: str,
    ) -> ParsedResponse:
        body = _require_json_object(response)
        candidates = body.get("candidates") or []
        if not candidates:
            raise ProviderError(
                "gemini response contained no candidates",
                provider=self.name,
                model=requested_model,
            )
        content = candidates[0].get("content") or {}
        parts = content.get("parts") or []
        text = "".join(
            part.get("text", "")
            for part in parts
            if isinstance(part, Mapping)
        )
        meta = body.get("usageMetadata") or {}
        usage = Usage(
            input_tokens=int(meta.get("promptTokenCount") or 0),
            output_tokens=int(meta.get("candidatesTokenCount") or 0),
        )
        model_used = body.get("modelVersion") or requested_model
        return ParsedResponse(text=text, usage=usage, model_used=model_used)
