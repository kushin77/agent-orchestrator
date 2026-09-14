"""Offline, deterministic chat-quality evaluation (issue #509).

Three things live here and they are deliberately small:

* :mod:`registry.chat.eval.cases` — the committed fixture set, ``cases.yaml``:
  five declared cases, one per quality risk, each with a declared expectation.
* :mod:`registry.chat.eval.standins` — the deterministic stand-ins for the
  collaborators a chat turn depends on (the model, grounding, the guards,
  tenant scoping). No clock, no randomness, no network.
* :mod:`registry.chat.eval.harness` — the runner: it drives the stand-ins over
  the fixture set and fails a case *by name* when its declared expectation is
  not met. A case that could not be run is ``CANNOT_ASSESS``, never a pass.
"""

from __future__ import annotations

__all__ = ["harness", "standins"]
