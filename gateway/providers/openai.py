"""OpenAI adapter (issue #15) - covers Copilot/GPT deployments.

The ``openai`` adapter speaks the OpenAI ``chat/completions`` protocol
(``POST {base}/chat/completions``, Bearer auth). Because Copilot and
enterprise GPT deployments are reachable through OpenAI-compatible gateways,
the same adapter serves any compatible base URL via ``ProviderConfig``
(override ``base_url`` per deployment/tenant). Model tiering on the issue-#9
ladder: ``LOW``/``MED`` -> ``gpt-4o-mini``, ``HIGH``/``MAX`` -> ``gpt-4o``.

---knowledge---
module_id: gateway.providers.openai
system: gateway
app: providers
solution_class: enterprise
patterns: [shared-compat-base, per-deployment-base-url]
derives_from: gateway/providers/base.py
owner_sme: platform-sme
tier: L0
interfaces: [OpenAIProvider]
invariants: "the adapter speaks the OpenAI chat/completions protocol over the injected transport"
gotchas: "the same adapter serves any OpenAI-compatible base URL through ProviderConfig, which is why it also covers Copilot and GPT deployments"
related: ["#15"]
do_not_duplicate: gateway/providers/base.py
---knowledge---
"""

from __future__ import annotations

from providers.base import OpenAICompatProvider


class OpenAIProvider(OpenAICompatProvider):
    """OpenAI / Copilot / GPT chat adapter (OpenAI-compatible protocol)."""

    name = "openai"
