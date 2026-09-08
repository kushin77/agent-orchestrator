"""Local Ollama adapter (issue #15) - the keyless, on-prem fallback target.

Wire protocol (``POST {base}/api/chat``): plain chat messages (Ollama accepts
the ``system`` role inline), ``stream: false``, sampling under ``options``.
Response usage maps ``prompt_eval_count`` / ``eval_count`` (the resilient
ollama client semantics). No API key is required (``requires_key=false``);
the local endpoint is the graceful-degradation target for every cloud
provider (cloud -> local, the defragsuite + gov-ai-scout pattern).
"""

from __future__ import annotations

from typing import Any

from providers.base import (
    HttpModelProvider,
    ParsedResponse,
    _require_json_object,
)
from providers.config import ProviderConfig
from providers.contract import ChatMessage, ChatOptions, Usage
from providers.errors import ProviderError
from providers.transport import HttpTransport, HttpResponse


class OllamaProvider(HttpModelProvider):
    """Local Ollama chat adapter."""

    name = "ollama"

    def __init__(
        self,
        config: ProviderConfig,
        transport: HttpTransport,
        credentials=None,
    ) -> None:
        super().__init__(config, transport, credentials)

    def build_payload(
        self,
        messages: list[ChatMessage],
        model: str,
        options: ChatOptions,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "stream": False,
            "messages": [
                {"role": m.role, "content": m.content} for m in messages
            ],
        }
        sampling: dict[str, Any] = {}
        if options.temperature is not None:
            sampling["temperature"] = options.temperature
        if options.max_tokens is not None:
            sampling["num_predict"] = options.max_tokens
        if sampling:
            payload["options"] = sampling
        return payload

    def parse_response(
        self,
        response: HttpResponse,
        requested_model: str,
    ) -> ParsedResponse:
        body = _require_json_object(response)
        message = body.get("message") or {}
        text = message.get("content") or ""
        usage = Usage(
            input_tokens=int(body.get("prompt_eval_count") or 0),
            output_tokens=int(body.get("eval_count") or 0),
        )
        model_used = body.get("model") or requested_model
        if not text:
            raise ProviderError(
                "ollama response contained no message content",
                provider=self.name,
                model=requested_model,
            )
        return ParsedResponse(text=text, usage=usage, model_used=model_used)
