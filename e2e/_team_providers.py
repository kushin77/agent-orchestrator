"""Offline provider adapters for the purebliss team (issue #257, superseded).

The six-agent team (epic #253 + issue #340) maps to providers as follows
(frozen contract):

    ollama    -> ollama    (gateway/providers/ollama.py)
    deepseek  -> deepseek  (gateway/providers/deepseek.py)
    claude    -> anthropic (gateway/providers/anthropic.py)
    hermes    -> hermes    (gateway/providers/hermes.py; falls back to ollama)
    paperclip -> paperclip (gateway/providers/paperclip.py)
    copilot   -> copilot   (gateway/providers/copilot.py; reuses the OpenAI
                            adapter — OpenAI-compatible, no stub needed)

Since issue #255 landed the REAL ``gateway/providers/{paperclip,hermes}.py``
adapters, this module's stub adapters are a documented FALLBACK only: they are
installed by ``e2e/wiring.install_team_provider_stubs`` **only if** the real
adapters are absent from ``PROVIDER_CLASSES`` (that guard is a no-op today).
They mirror the real wire shapes exactly — paperclip is OpenAI-compatible
(``chat/completions``) and hermes is Ollama-compatible (``/api/chat``) — so a
fallback can never disagree with the scriptable transport rig that
``gateway/proxy/wiring.ProviderRig`` drives with the same shapes.

copilot (issue #340) needs no stub here: its real adapter is always registered
in ``PROVIDER_CLASSES`` and inherits the OpenAI adapter's wire handling.
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
