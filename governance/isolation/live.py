"""A live projection of real lane state — exposed through `cli.py audit --live` (issue #885).

---knowledge---
module_id: governance.isolation.live
system: governance
app: isolation
solution_class: pattern
patterns: [deterministic, lane-isolation]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [LiveLane, project, render]
invariants: ""
gotchas: ""
related: ["#885"]
do_not_duplicate: null
---knowledge---

Every other read in this package answers "what does the record say" (a lane's
minted identity, a speculative-base attestation). This module re-derives a
projection of that same state **from the real worktrees on disk, right now** —
the current branch a worktree is actually on, whether the worktree still
exists, and whether a recorded speculative attestation still matches the
branch git currently reports for that lane — and reports drift between the two
by name. It adds no new top-level CLI verb: it is reached only through the
existing ``audit`` verb's ``--live`` flag (``cli.py``), so
``scripts/check-control-verbs.sh`` sees no unregistered surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import speculative
from .identity import SessionIdentity
from .worktree import git, list_records


@dataclass(frozen=True)
class LiveLane:
    """One lane's recorded identity, next to what git reports right now."""

    session_id: str
    branch: str
    worktree_exists: bool
    current_branch: str
    branch_drift: bool
    speculative_drift: str

    def as_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "branch": self.branch,
            "worktree_exists": self.worktree_exists,
            "current_branch": self.current_branch,
            "branch_drift": self.branch_drift,
            "speculative_drift": self.speculative_drift,
        }


def _current_branch(worktree: Path) -> str:
    result = git(worktree, "symbolic-ref", "--short", "-q", "HEAD")
    return result.stdout.strip() if result.returncode == 0 else ""


def _speculative_drift(main: Path | str, identity: SessionIdentity) -> str:
    """"" (no claim), "clean", or a named drift, without mutating any record.

    Deliberately read-only: `speculative.verify()` is the enforcement path
    (it raises/reports Violations); this reports the same fact as a live
    status string for a human or another projection to read, and never
    re-derives or re-writes the attestation itself.
    """
    attestation = speculative.read(main, identity.session_id)
    if attestation is None:
        return ""
    try:
        actual_git_sha = git(main, "rev-parse", identity.branch).stdout.strip()
    except Exception:  # pragma: no cover - defensive; git() does not raise
        actual_git_sha = ""
    if not actual_git_sha:
        return "branch-unmeasurable"
    if actual_git_sha != attestation.git_sha:
        return "attestation-git-sha-stale"
    return "clean"


def project(main: Path | str) -> list[LiveLane]:
    """One :class:`LiveLane` per recorded identity, measured against real git."""
    projected: list[LiveLane] = []
    for identity in list_records(main):
        worktree = identity.worktree
        exists = worktree.exists()
        current = _current_branch(worktree) if exists else ""
        projected.append(
            LiveLane(
                session_id=identity.session_id,
                branch=identity.branch,
                worktree_exists=exists,
                current_branch=current,
                branch_drift=exists and current != identity.branch,
                speculative_drift=_speculative_drift(main, identity),
            )
        )
    return projected


def render(main: Path | str) -> str:
    """A short, deterministic text projection for ``cli.py audit --live``."""
    lines = []
    for lane in project(main):
        status = "OK"
        notes = []
        if not lane.worktree_exists:
            status = "GONE"
            notes.append("worktree missing")
        elif lane.branch_drift:
            status = "DRIFT"
            notes.append(f"on {lane.current_branch!r}, recorded {lane.branch!r}")
        if lane.speculative_drift == "attestation-git-sha-stale":
            status = "DRIFT"
            notes.append("speculative attestation git_sha is stale")
        elif lane.speculative_drift == "branch-unmeasurable":
            status = "UNMEASURABLE"
            notes.append("branch tip could not be resolved")
        suffix = f" ({'; '.join(notes)})" if notes else ""
        lines.append(f"  {status:<12} lane {lane.session_id} [{lane.branch}]{suffix}")
    return "\n".join(lines)
