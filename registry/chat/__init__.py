"""Chat quality loop — prompt modules, eval harness and the feedback loop.

Issue ``kushin77/agent-orchestrator#509``, the quality half of the
conversational surface (parent ``#500``, contract ``ADR-0023``). The package
owns four things and nothing else:

* :mod:`registry.chat.prompt_modules` — the versioned, frozen chat prompt
  modules and their declared output schemas. A chat module's schema **requires
  the citations envelope** (:mod:`registry.chat.envelope`), and the registry
  refuses a module whose schema does not, so "this answer names its sources" is
  checkable rather than aspirational.
* :mod:`registry.chat.eval` — an offline, deterministic eval harness over a
  fixed fixture set with **declared** expectations. An unmet expectation fails
  the case *by name*; a case the harness could not run is ``CANNOT_ASSESS``,
  never a pass (AO-GR-19).
* :mod:`registry.chat.feedback` — per-message feedback keyed to
  ``(prompt module version, model, tier)`` and projected into the **existing**
  per-version FP/FN metrics vocabulary (``registry/prompts/feedback.py``).
* :mod:`registry.chat.regression` — the promotion gate: a changed module
  version is re-evaluated against the fixtures, and a version that regresses a
  case fails *by name* before it is promoted.

Nothing here calls a model, reads the network or touches the wall clock: the
harness is offline and deterministic by construction. ``registry/chat/**`` is
this lane; ``registry/prompts/**``, ``registry/profiles/**`` and
``registry/personas/**`` are read-only references, consumed for their house
shape and never edited.
"""

from __future__ import annotations

__all__ = ["__version__"]

#: Package version. The chat prompt modules carry their own, independent
#: versions (``chat-answer@v1``), which is what the eval gate pins.
__version__ = "1.0.0"
