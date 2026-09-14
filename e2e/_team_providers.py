"""Offline provider adapters for the purebliss team (issue #257, superseded).

The five-agent team (epic #253) maps to providers as follows (frozen contract):

    ollama    -> ollama    (gateway/providers/ollama.py)
    deepseek  -> deepseek  (gateway/providers/deepseek.py)
    claude    -> anthropic (gateway/providers/anthropic.py)
    hermes    -> hermes    (gateway/providers/hermes.py; falls back to ollama)
    paperclip -> paperclip (gateway/providers/paperclip.py)

Since issue #255 landed the REAL ``gateway/providers/{paperclip,hermes}.py``
adapters, this module's stub adapters are a documented FALLBACK only: they are
installed by ``e2e/wiring.install_team_provider_stubs`` **only if** the real
adapters are absent from ``PROVIDER_CLASSES`` (that guard is a no-op today).
They mirror the real wire shapes exactly — paperclip is OpenAI-compatible
(``chat/completions``) and hermes is Ollama-compatible (``/api/chat``) — so a
fallback can never disagree with the scriptable transport rig that
``gateway/proxy/wiring.ProviderRig`` drives with the same shapes.
"""

from __future__ import annotations

from providers.base import OpenAICompatProvider
from providers.ollama import OllamaProvider


class PaperclipProvider(OpenAICompatProvider):
    """Offline fallback for the paperclip team member (OpenAI-compatible)."""

    name = "paperclip"


class HermesProvider(OllamaProvider):
    """Offline fallback for the hermes team member (Ollama-compatible)."""

    name = "hermes"
