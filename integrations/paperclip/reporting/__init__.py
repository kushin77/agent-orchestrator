"""Paperclip's reporting half: each mandatory module's distributable brief (issue #447).

The brief is the organized, per-module statement of *what every repo must
carry, at which pin, and whether it is current*. It is composed **only** from
the ecosystem module registry (``governance/modules/``, issue #445) and the hub
files that registry cites — never by re-deriving mandatory status here.

* :mod:`integrations.paperclip.reporting.capability` — the contract binding the
  persona's declaration to the tools the capability actually needs;
* :mod:`integrations.paperclip.reporting.composer` — the deterministic
  composition, in which every line is a claim carrying the registry row or the
  cited path it resolves to;
* :mod:`integrations.paperclip.reporting.model` — the vocabulary (the three
  states and the ``not-a-module`` refusal are *imported* from the registry, not
  restated) plus the claim book and citation resolution;
* :mod:`integrations.paperclip.reporting.cli` — compose / check / claims /
  capability, tri-state.

The frozen artifact is ``docs/MODULE-BRIEF.md``; the gate that regenerates it
and refuses a stale or unsupported one is ``scripts/check-module-brief.sh``.
"""

from __future__ import annotations

__all__ = ["capability", "cli", "composer", "model"]
