"""Session reconciliation — heartbeats, TTL sweep, teardown, lock release (#304).

---knowledge---
module_id: governance.reconcile
system: governance
app: reconcile
solution_class: template
patterns: [lane-isolation, explain-every-artifact]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: []
invariants: ""
gotchas: ""
related: ["#304", "#628"]
do_not_duplicate: null
---knowledge---

`governance/isolation` opens a lane and `governance/lifecycle` closes one that
finished. This package handles the third case: a lane whose agent **died**. See
``README.md`` for the contract, ``heartbeat.py`` for detection, ``sweep.py`` for
the teardown safety rule and ``audit.py`` for the worktree/branch audit (#628)
that reports every artifact no record explains, without removing anything.
"""

from __future__ import annotations

from .audit import (
    BRANCH,
    EXEMPT,
    MATCHED,
    UNMATCHED,
    WORKTREE,
    Artifact,
    AuditOps,
    AuditReport,
    AuditUnavailable,
    Explanation,
    WorktreeEntry,
    audit,
    parse_worktrees,
    read_beats,
)
from .audit import describe as describe_audit
from .heartbeat import (
    DEFAULT_BEAT_SECONDS,
    DEFAULT_TTL_MINUTES,
    LIVE,
    ORPHAN,
    SHELVED,
    SUSPECT,
    Beater,
    Session,
    Verdict,
    clear,
    judge,
    list_sessions,
    read,
    stamp,
)
from .sweep import (
    PARKED,
    RECLAIMED,
    Action,
    ReconcileOps,
    RepoOps,
    Step,
    SweepReport,
    describe,
    sweep,
)

__all__ = [
    "BRANCH",
    "DEFAULT_BEAT_SECONDS",
    "DEFAULT_TTL_MINUTES",
    "EXEMPT",
    "LIVE",
    "MATCHED",
    "ORPHAN",
    "PARKED",
    "RECLAIMED",
    "SHELVED",
    "SUSPECT",
    "UNMATCHED",
    "WORKTREE",
    "Action",
    "Artifact",
    "AuditOps",
    "AuditReport",
    "AuditUnavailable",
    "Beater",
    "Explanation",
    "ReconcileOps",
    "RepoOps",
    "Session",
    "Step",
    "SweepReport",
    "Verdict",
    "WorktreeEntry",
    "audit",
    "clear",
    "describe",
    "describe_audit",
    "judge",
    "list_sessions",
    "parse_worktrees",
    "read",
    "read_beats",
    "stamp",
    "sweep",
]
