"""C-suite instruction-layer mirrors (issue #643, workbook-12).

Derives one canonical instruction source per C-suite seat (``ceo``, ``cto``,
``coo``, ``cfo``, ``cmo``) from the workbook-1 PersonaCards and the workbook-8
prompt modules already on ``master``, renders each into the four per-tool
mirrors the ``aoi`` layer defines, and proves with a conformance suite that the
SAME rules and precedence reach every harness.

The derivation is the single source of truth (never a hand-written second
copy): ``python3 -m csuite.derive --write`` regenerates the canonical sources,
the per-tool mirrors and the pinned consumer states byte-for-byte.
"""

from __future__ import annotations

__all__ = ["derive"]

__version__ = "1.0.0"
