"""Paperclip provider adapter (issue #255) - OpenAI-compatible ``chat/completions``.

Paperclip is the vendored planning / status-report workflow module
(``vendor/CMR/catalog/modules/paperclip``, issue #124). Its inference surface
speaks the OpenAI-compatible ``chat/completions`` wire shape (Bearer auth,
``choices[0].message.content``, ``usage.prompt_tokens`` / ``completion_tokens``),
so the request/response handling is inherited from ``OpenAICompatProvider`` —
the same shared base the ``openai`` and ``deepseek`` adapters use (provenance:
gov-ai-scout provider gateway + llm-triage provider-neutral ABC, recorded in
``docs/CANNIBALIZATION.md``).

The tier -> model ids are default illustrative identifiers for the vendored
module; operators override them via per-tenant model mapping exactly like every
other provider config (``providers.config.TenantModelMapping``).

---knowledge---
module_id: gateway.providers.paperclip
system: gateway
app: providers
solution_class: enterprise
patterns: [inherit-shared-base, vendored-module-adapter, overridable-model-ids]
derives_from: gateway/providers/base.py
owner_sme: platform-sme
tier: L0
interfaces: [PaperclipProvider]
invariants: "request and response handling is inherited from the shared OpenAI-compatible base"
gotchas: "the tier to model ids are illustrative defaults for the vendored module and are overridable through per-tenant model mapping"
related: ["#255", "#124"]
do_not_duplicate: gateway/providers/base.py
---knowledge---
"""

from __future__ import annotations

from providers.base import OpenAICompatProvider


class PaperclipProvider(OpenAICompatProvider):
    """Paperclip planning/status-report agent adapter (OpenAI-compatible)."""

    name = "paperclip"
