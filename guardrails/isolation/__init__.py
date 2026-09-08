"""Isolation integrity — detect-first / opt-in repair (issue #30).

Tenant isolation integrity for the control plane, on the detect-first /
opt-in-repair doctrine (cannibalized from ``capital-underwriting``'s
``accountIsolationRepair`` service and ``saas-rbac``'s scope-separate-from-
permission doctrine; see ``PROVENANCE.md``):

* **detect** — a static AST scanner of tenant-store surfaces (scope-drop
  reads/writes, cross-tenant fallbacks, shared mutable state) plus a
  data-integrity detector over tenant-scoped datasets (orphaned records,
  fallback-tenant pile-up, cross-tenant duplicates) plus runtime per-tenant
  cross-tenant read/write probes.  Nothing mutates.
* **triage** — every finding maps by severity to auto-BLOCK (critical/high)
  or an SME-reviewer queue (medium) or LOG (low); there is no silent path.
* **repair** — opt-in, transactional (validate-everything-then-commit),
  audited (an in-state ledger), idempotent.  Dry-run is the default; nothing
  changes without an explicit ``--apply``.

Verdicts use the guard-honesty tri-state vocabulary (issue #28, consumed from
the sibling ``guardrails/honesty`` lane): OK / NOT-OK / CANNOT-ASSESS, where
CANNOT-ASSESS never reads as a pass.  Everything is offline, deterministic
and depends only on the Python standard library.
"""

from __future__ import annotations

from .model import Finding, FindingCategory, IndexVerdict, Severity
from .tristate import TriState as _TriState

__all__ = [
    "Finding",
    "FindingCategory",
    "IndexVerdict",
    "Severity",
]

__version__ = "0.1.0"
