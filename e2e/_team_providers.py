"""Offline stub provider adapters for the purebliss team (issue #257).

The five-agent team (epic #253) maps to providers as follows (frozen contract):

    ollama    -> ollama    (exists: gateway/providers/ollama.py)
    deepseek  -> deepseek  (exists)
    claude    -> anthropic (exists)
    hermes    -> hermes    (local; falls back to ollama)
    paperclip -> paperclip (local)

``paperclip`` and ``hermes`` are keyless local services with no live endpoint,
so the e2e lane provides OFFLINE STUB adapters that speak the same
``HttpModelProvider`` contract as the merged providers and are driven through
the gateway's scriptable transport rig (matching the existing conformance
pattern). No gateway file is edited; the stubs are registered at runtime by
``e2e/wiring.install_team_provider_stubs``.
"""

from __future__ import annotations

from typing import Any, Mapping

from providers.base import (
    HttpModelProvider,
    ParsedResponse,
    _require_json_object,
)
from providers.contract import ChatMessage, ChatOptions, Usage
from providers.errors import ProviderError
from providers.transport import HttpResponse


class _LocalTeamStub(HttpModelProvider):
    """Keyless local hop for a team provider without a live endpoint.

    Mirrors the Ollama local adapter (``requires_key=False``, local base URL)
    but parses the canned response shape the gateway transport rig emits for
    providers it does not hard-code (``candidates`` / ``usageMetadata`` — the
    Gemini shape).
    """

    name = "abstract-team-stub"

    def build_payload(
        self,
        messages: list[ChatMessage],
        model: str,
        options: ChatOptions,
    ) -> dict[str, Any]:
        return {
            "model": model,
            "stream": False,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in messages
            ],
        }

    def parse_response(
        self,
        response: HttpResponse,
        requested_model: str,
    ) -> ParsedResponse:
        body = _require_json_object(response)
        candidates = body.get("candidates") or []
        if not candidates:
            raise ProviderError(
                f"{self.name} response contained no candidates",
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


class PaperclipProvider(_LocalTeamStub):
    """Offline stub for the paperclip team member (local)."""

    name = "paperclip"


class HermesProvider(_LocalTeamStub):
    """Offline stub for the hermes team member (local; ollama fallback)."""

    name = "hermes"
