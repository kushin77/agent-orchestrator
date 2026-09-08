"""OpenAI adapter (issue #15) - covers Copilot/GPT deployments.

The ``openai`` adapter speaks the OpenAI ``chat/completions`` protocol
(``POST {base}/chat/completions``, Bearer auth). Because Copilot and
enterprise GPT deployments are reachable through OpenAI-compatible gateways,
the same adapter serves any compatible base URL via ``ProviderConfig``
(override ``base_url`` per deployment/tenant). Model tiering on the issue-#9
ladder: ``LOW``/``MED`` -> ``gpt-4o-mini``, ``HIGH``/``MAX`` -> ``gpt-4o``.
"""

from __future__ import annotations

from providers.base import OpenAICompatProvider


class OpenAIProvider(OpenAICompatProvider):
    """OpenAI / Copilot / GPT chat adapter (OpenAI-compatible protocol)."""

    name = "openai"
