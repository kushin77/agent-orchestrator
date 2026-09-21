"""DeepSeek adapter (issue #15) - OpenAI-compatible ``chat/completions``.

DeepSeek exposes the OpenAI wire shape (``POST {base}/chat/completions``,
Bearer auth, ``choices[0].message.content``, ``usage.prompt_tokens`` /
``completion_tokens``), so the request/response handling is inherited from
``OpenAICompatProvider``. Models map to the issue-#9 tiers:

- ``LOW``/``MED`` -> ``deepseek-chat`` (flash-class, the platform default),
- ``HIGH``/``MAX`` -> ``deepseek-reasoner`` (pro-class).

---knowledge---
module_id: gateway.providers.deepseek
system: gateway
app: providers
solution_class: enterprise
patterns: [inherit-shared-base, consumed-tier-vocabulary]
derives_from: gateway/providers/base.py
owner_sme: platform-sme
tier: L0
interfaces: [DeepSeekProvider]
invariants: "request and response handling is inherited from the shared OpenAI-compatible base rather than re-implemented"
gotchas: "LOW and MED map to deepseek-chat and HIGH and MAX to deepseek-reasoner"
related: ["#15"]
do_not_duplicate: gateway/providers/base.py
---knowledge---
"""

from __future__ import annotations

from providers.base import OpenAICompatProvider


class DeepSeekProvider(OpenAICompatProvider):
    """DeepSeek chat adapter (OpenAI-compatible protocol)."""

    name = "deepseek"
