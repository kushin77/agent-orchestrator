"""DeepSeek adapter (issue #15) - OpenAI-compatible ``chat/completions``.

DeepSeek exposes the OpenAI wire shape (``POST {base}/chat/completions``,
Bearer auth, ``choices[0].message.content``, ``usage.prompt_tokens`` /
``completion_tokens``), so the request/response handling is inherited from
``OpenAICompatProvider``. Models map to the issue-#9 tiers:

- ``LOW``/``MED`` -> ``deepseek-chat`` (flash-class, the platform default),
- ``HIGH``/``MAX`` -> ``deepseek-reasoner`` (pro-class).
"""

from __future__ import annotations

from providers.base import OpenAICompatProvider


class DeepSeekProvider(OpenAICompatProvider):
    """DeepSeek chat adapter (OpenAI-compatible protocol)."""

    name = "deepseek"
