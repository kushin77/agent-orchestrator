"""Hermes provider adapter (issue #255) - Ollama-compatible ``/api/chat``.

Hermes is the vendored agent-routing / capability-registry / model-tiering
module (``vendor/CMR/catalog/modules/hermes-agents``, issue #124). Its local
inference endpoint speaks the Ollama-compatible ``/api/chat`` wire shape
(``stream: false``, ``message.content``, ``prompt_eval_count`` / ``eval_count``),
so the request/response handling is inherited from ``OllamaProvider`` — the
same adapter that serves the local Ollama runtime (provenance: kushin77/ollama
resilient client, recorded in ``docs/CANNIBALIZATION.md``).

The frozen team mapping (EPIC #253) routes ``hermes -> hermes/ollama``: hermes
is the primary local hop and local Ollama is its terminal fallback, declared on
the provider config in ``providers.config.default_provider_configs()``.
"""

from __future__ import annotations

from providers.ollama import OllamaProvider


class HermesProvider(OllamaProvider):
    """Hermes local agent-service inference adapter (Ollama-compatible)."""

    name = "hermes"
