"""Copilot adapter (issue #340) - OpenAI-compatible ``chat/completions``.

GitHub Copilot's chat surface is OpenAI-compatible (``POST {base}/chat/completions``,
Bearer auth, ``choices[0].message.content``, ``usage.prompt_tokens`` /
``usage.completion_tokens``), so this adapter carries NO new request/response
logic: it is an explicit **mapping of the ``copilot`` provider id onto the
existing ``OpenAIProvider`` adapter** (``gateway/providers/openai.py``), which
the platform already documents as covering the "Copilot/GPT" deployments.
``CopilotProvider`` only rebinds that adapter to the ``copilot`` provider id, so
copilot becomes a first-class provider with its own config, rate card, catalog
module and pane-of-glass identity while the inherited wire handling stays shared
with OpenAI byte-for-byte.

Model ids map to the issue-#9 tiers exactly as the OpenAI adapter does:
``LOW``/``MED`` -> ``gpt-4o-mini``, ``HIGH``/``MAX`` -> ``gpt-4o``. These are the
illustrative Copilot-served model identifiers (same convention the paperclip and
hermes configs use); operators override them per tenant through
``providers.config.TenantModelMapping``.

Decision record (issue #340, deliverable 4): reusing — not re-implementing — the
existing OpenAI adapter is the documented "mapping" option. A distinct provider
id is still required (rather than aliasing the ``openai`` provider) because the
metering intake prices a call by the gateway record's *provider* id, so the
copilot rate card (``telemetry/metering/rate_cards/copilot.yaml``) and the
gateway catalog module (``gateway/catalog/modules/copilot/module.json``) are
keyed on it, and the pane of glass must show copilot as its own agent hop.

---knowledge---
module_id: gateway.providers.copilot
system: gateway
app: providers
solution_class: enterprise
patterns: [rebind-not-reimplement, shared-wire-handling, first-class-provider]
derives_from: gateway/providers/openai.py
owner_sme: platform-sme
tier: L0
interfaces: [CopilotProvider]
invariants: "the adapter carries no new request or response logic: it rebinds the OpenAI adapter to the copilot provider id"
gotchas: "the inherited wire handling stays shared with OpenAI byte-for-byte, so the two cannot drift"
related: ["#340"]
do_not_duplicate: gateway/providers/openai.py
---knowledge---
"""

from __future__ import annotations

from providers.openai import OpenAIProvider


class CopilotProvider(OpenAIProvider):
    """GitHub Copilot chat adapter (OpenAI-compatible; reuses OpenAIProvider)."""

    name = "copilot"
