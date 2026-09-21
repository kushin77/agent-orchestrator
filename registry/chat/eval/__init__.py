"""Offline, deterministic chat-quality evaluation (issue #509).

---knowledge---
module_id: registry.chat.eval
system: registry
app: eval
solution_class: class
patterns: [package-contract, offline-deterministic]
derives_from: null
owner_sme: qa-sme
tier: L0
interfaces: [the registry.chat.eval package surface, harness, standins]
invariants: "the eval package may call no model, no retrieval service, no guardrail and no network"
gotchas: ""
related: ["#509"]
do_not_duplicate: null
---knowledge---

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
