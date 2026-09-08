"""Integrity self-check against this repo's own tenant-scoped modules.

Acceptance-criterion scan: the isolation lane runs its own detector over the
platform's tenant-scoped store surfaces — ``registry/service``,
``identity/rbac``, ``gateway/mcp`` and ``engine/memory`` — read-only
(static AST; nothing is imported or mutated).  The report must be clean or
produce concrete findings, and it is deterministic, so a future store change
that drops a tenant dimension is caught by the self-check before it ships.

Coverage is reported honestly: a store the analyzer can fully model is
verdict-``OK`` only when every access to its tenant-dimensioned index carries
the tenant dimension; an id-keyed / composite-derived store is enumerated as
``CANNOT-ASSESS`` (never hidden, never a pass).  ``tests``/``__pycache__``
directories are excluded — test code deliberately probes isolation
boundaries and is not the product's tenant-store surface.
"""

from __future__ import annotations

import os
from typing import List, Optional

from .model import ScanReport
from .scanner import scan_paths
from .tristate import TriState

#: The platform's tenant-scoped module subtrees the self-check owns scanning.
DEFAULT_SELFCHECK_SUBTREES = [
    "registry/service",
    "identity/rbac",
    "gateway/mcp",
    "engine/memory",
]

#: Directories pruned while walking (test + bytecode artifacts).
_EXCLUDED_DIRS = ("tests", "__pycache__")


def repo_root(start: Optional[str] = None) -> str:
    """Walk up from ``start`` (or this file) to the repo root (has AGENTS.md)."""
    here = start or os.path.dirname(os.path.abspath(__file__))
    current = os.path.abspath(here)
    while True:
        if os.path.isfile(os.path.join(current, "AGENTS.md")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            raise RuntimeError("could not locate repo root (no AGENTS.md)")
        current = parent


def self_check(root: Optional[str] = None,
               subtrees: Optional[List[str]] = None) -> ScanReport:
    """Run the code anti-pattern scanner over the tenant-scoped subtrees.

    Returns a :class:`ScanReport`; ``report.aggregate()`` is the honest
    tri-state.  Read-only — nothing under the subtrees is imported or
    modified.
    """
    root = root or repo_root()
    subtrees = subtrees or DEFAULT_SELFCHECK_SUBTREES
    paths = [os.path.join(root, subtree) for subtree in subtrees]
    return scan_paths(paths, base=root, exclude_dirs=_EXCLUDED_DIRS)


def self_check_exit(report: ScanReport, *, strict: bool = False) -> int:
    """Exit code for the self-check.

    Default (review/CI helper): ``1`` when any NOT-OK finding exists, else
    ``0`` — the check's claim is "no detected tenant-isolation anti-patterns
    in the modeled tenant-store surface", with CANNOT-ASSESS surfaces
    reported in the text, never hidden.

    ``strict`` applies the guard-honesty contract end-to-end: any
    CANNOT-ASSESS surface keeps the check from passing (exit ``2``), exactly
    as a guard that cannot assess never reads as a pass.
    """
    if report.has_findings:
        return 1
    if strict and any(v.verdict is TriState.CANNOT_ASSESS
                      for v in report.verdicts):
        return 2
    return 0
