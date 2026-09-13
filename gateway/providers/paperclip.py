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
"""

from __future__ import annotations

from providers.base import OpenAICompatProvider


class PaperclipProvider(OpenAICompatProvider):
    """Paperclip planning/status-report agent adapter (OpenAI-compatible)."""

    name = "paperclip"
