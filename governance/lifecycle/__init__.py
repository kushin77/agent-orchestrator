"""End-to-end GitHub lifecycle: every work item closes hygienically (issue #269).

``governance/isolation`` guarantees a lane *opens* correctly; this package covers
the other half — the **close**. See ``README.md`` for the contract, ``model.py``
for the invariants, ``audit.py`` for what is broken, and ``closeout.py`` for how
it gets fixed.
"""

from __future__ import annotations

from .audit import Finding, Quarantine, audit, audit_item, hygiene, load_quarantine
from .closeout import CloseOutResult, Step, closeout, describe
from .model import INVARIANTS, INVARIANTS_BY_CODE, ITEM_INVARIANTS, STAGES, TERMINAL_STAGE, invariant, stage_of

__all__ = [
    "INVARIANTS",
    "INVARIANTS_BY_CODE",
    "ITEM_INVARIANTS",
    "STAGES",
    "TERMINAL_STAGE",
    "CloseOutResult",
    "Finding",
    "Quarantine",
    "Step",
    "audit",
    "audit_item",
    "closeout",
    "describe",
    "hygiene",
    "invariant",
    "load_quarantine",
    "stage_of",
]
