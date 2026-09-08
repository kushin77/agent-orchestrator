"""Shared offline helpers for the provider adapter tests (not a test module).

Each provider adapter is exercised against a ``RecordingTransport`` scripted
with a valid provider-specific response body, so no test ever touches the
network. The body shapes here mirror each provider's real wire protocol
(Anthropic Messages API, OpenAI-compatible ``chat/completions`` for
openai+deepseek, Gemini ``generateContent``, Ollama ``/api/chat``).
"""

from __future__ import annotations

import json
import os

from providers.config import ProviderConfig, default_provider_configs
from providers.transport import HttpResponse

SCHEMA_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "fixtures", "schemas", "sentiment.schema.json"
)


def load_sentiment_schema() -> dict:
    with open(SCHEMA_FILE, "r", encoding="utf-8") as fh:
        return json.load(fh)


def chat_body_for(provider: str, model: str, text: str) -> dict:
    """A valid provider-specific response body containing ``text``."""
    if provider == "anthropic":
        return {
            "content": [{"type": "text", "text": text}],
            "usage": {"input_tokens": 12, "output_tokens": 7},
            "model": model,
        }
    if provider in ("openai", "deepseek"):
        return {
            "choices": [{"message": {"content": text}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 7},
            "model": model,
        }
    if provider == "gemini":
        return {
            "candidates": [{"content": {"parts": [{"text": text}]}}],
            "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 7},
            "modelVersion": model,
        }
    if provider == "ollama":
        return {
            "message": {"content": text},
            "prompt_eval_count": 12,
            "eval_count": 7,
            "model": model,
        }
    raise ValueError(f"no canned body for provider {provider!r}")


def ok_response(provider: str, model: str, text: str) -> HttpResponse:
    """A canned 200 response whose body matches ``provider``'s wire shape."""
    return HttpResponse(
        status=200,
        headers={"content-type": "application/json"},
        body=json.dumps(chat_body_for(provider, model, text)),
    )


def config_for(provider: str) -> ProviderConfig:
    return default_provider_configs()[provider]


def expected_endpoint(provider: str, model: str) -> str:
    """Mirror each adapter's endpoint construction for request assertions."""
    cfg = config_for(provider)
    base = cfg.base_url.rstrip("/")
    if provider == "gemini":
        return f"{base}/models/{model}:generateContent"
    return f"{base}{cfg.api_path}"


def auth_header_name(provider: str) -> str | None:
    """The header that should carry the API key, or None for keyless."""
    return {
        "anthropic": "x-api-key",
        "gemini": "x-goog-api-key",
        "openai": "authorization",
        "deepseek": "authorization",
        "ollama": None,
    }[provider]
