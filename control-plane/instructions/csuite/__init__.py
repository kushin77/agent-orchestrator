"""C-suite instruction-layer mirrors (issue #643, workbook-12).

Derives one canonical instruction source per C-suite seat (``ceo``, ``cto``,
``coo``, ``cfo``, ``cmo``) from the workbook-1 PersonaCards and the workbook-8
prompt modules already on ``master``, renders each into the four per-tool
mirrors the ``aoi`` layer defines, and proves with a conformance suite that the
SAME rules and precedence reach every harness.

The derivation is the single source of truth (never a hand-written second
copy): ``python3 -m csuite.derive --write`` regenerates the canonical sources,
the per-tool mirrors and the pinned consumer states byte-for-byte.


---knowledge---
module_id: control-plane.instructions.csuite
system: control-plane
app: instructions
solution_class: class
patterns: [package-contract, public-surface, derived-never-authored]
derives_from: control-plane/instructions/aoi/__init__.py
owner_sme: docs-sme
tier: L0
interfaces: [csuite.derive]
invariants: "the derivation is the single source of truth and never a hand-written second copy, so regenerating reproduces the canonical sources, mirrors and consumer states byte-for-byte"
gotchas: "the five seats are derived from the workbook-1 PersonaCards and the workbook-8 prompt modules already on master, which stay read-only"
related: ["#643"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

__all__ = ["derive"]

__version__ = "1.0.0"
