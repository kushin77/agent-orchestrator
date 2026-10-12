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

NAMESAKE WARNING (measured in ``docs/decision-records/ADR-0012-hermes-paperclip-boundary.md``
§7): this provider is an Ollama-protocol endpoint serving the Hermes-3 *LLM* at
``http://localhost:8080``. It is NOT the ``hermes-agents`` routing/capability-
registry/tiering *service* (Flask, port 9501, ``/health`` ``/api/capabilities``
``/api/router`` ``/api/tiering``) that the rest of this repo's fleet-dispatch
layer adopts as the policy owner per ADR-0012. Nothing here talks to port 9501;
both the port and the model id are deliberately a different target sharing a
name. If you're trying to reach the routing service, this is the wrong file.

---knowledge---
module_id: gateway.providers.hermes
system: gateway
app: providers
solution_class: enterprise
patterns: [inherit-ollama-shape, vendored-module-adapter, declared-fallback]
derives_from: gateway/providers/ollama.py
owner_sme: platform-sme
tier: L0
interfaces: [HermesProvider]
invariants: "request and response handling is inherited from OllamaProvider, the same adapter that serves the local Ollama runtime"
gotchas: "the frozen team mapping routes hermes to hermes/ollama with local Ollama as its terminal fallback"
related: ["#255", "#124"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from providers.ollama import OllamaProvider


class HermesProvider(OllamaProvider):
    """Hermes local agent-service inference adapter (Ollama-compatible)."""

    name = "hermes"
