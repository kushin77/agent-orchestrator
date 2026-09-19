"""model.py — the record types the PR runner plans over (issue #1343).

Everything here is a plain frozen dataclass or a string constant: no I/O, no
subprocess. The planner (`plan.py`) is a pure function of these values, and the
transports (`verify.py`, `merge.py`) produce and consume them.

THE ONE KEY: (pr, head sha)
    Lesson 1 of the prototype day: evidence recorded per PR was worth nothing
    the moment a head was pushed — stale verifies and builds for old heads ate
    ~40% of the runner's capacity. So every evidence record and every live
    build names the exact head sha it is about, and the planner compares that
    sha against the PR's CURRENT head before it trusts either.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --- evidence states ---------------------------------------------------------
GREEN = "green"
RED = "red"
CANNOT_ASSESS = "cannot-assess"
PENDING = "pending"
EXPIRED = "expired"
CANCELLED = "cancelled"
PARKED = "parked"

#: States that are TERMINAL and NOT green and carry NO verdict about the tree:
#: the run never finished. Lesson 2 (#1267 class): these are re-queued by name,
#: never awaited. RED is deliberately absent — a red run DID finish and is a
#: verdict; re-running it burns capacity and changes nothing.
REQUEUE_STATES = frozenset({EXPIRED, CANCELLED, PARKED, CANNOT_ASSESS})

#: States the runner will wait on: a build/verify that is still running for the
#: CURRENT head. Anything else for the current head is either evidence or a
#: re-queue.
LIVE_STATES = frozenset({PENDING})

# --- evidence sources, ranked ------------------------------------------------
#: Lesson 5: sources are RANKED. A host-env red on the box must not block a merge
#: that Cloud Build proved; a Cloud Build green outranks a local red. Both are
#: recorded, the higher-ranked one is the basis. Lower number = higher rank.
SOURCE_CLOUD_BUILD = "cloud-build"
SOURCE_GATE_STATUS = "gate-status"
SOURCE_LOCAL = "local-marker"
SOURCE_RANK: dict[str, int] = {
    SOURCE_CLOUD_BUILD: 0,
    SOURCE_GATE_STATUS: 1,
    SOURCE_LOCAL: 2,
}

#: The gate-of-record context. ONE string, shared with `scripts/gate-status.sh`
#: (`AO_GATE_CONTEXT` default), `scripts/gate-status-map.py` (`CONTEXT`) and
#: `governance/platform/branch-protection.yaml` (`required_status_contexts`).
#: Lesson 10: a required check nothing posts blocks everyone forever, so the
#: parity of these strings is asserted by `fleet/runner/tests/test_plan.py`
#: and by `scripts/check-gate-status.sh`.
GATE_CONTEXT = "ao/gate-of-record"

#: The ONLY merge verb (lesson 4, #1266): never `gh pr merge` directly.
MERGE_VERB = "scripts/merge-pr.sh"
#: The merged-tree seam the merge consumes (lesson 3, #1254 step 6 / #1332).
MERGED_TREE_SEAM = "scripts/pr-queue.sh --check-merged-tree"

#: `scripts/verify.sh` exit codes that are not a verdict.
VERIFY_RC_PARKED_WORKTREE = 10
VERIFY_RC_PARKED_CAP = 11
PARKED_RCS = frozenset({VERIFY_RC_PARKED_WORKTREE, VERIFY_RC_PARKED_CAP})


@dataclass(frozen=True)
class OpenPR:
    """One open, non-draft PR as GitHub reports it right now."""

    number: int
    head_sha: str
    base_ref: str = "master"
    mergeable: str = "MERGEABLE"  # GitHub's MERGEABLE / CONFLICTING / UNKNOWN
    draft: bool = False
    title: str = ""


@dataclass(frozen=True)
class Evidence:
    """One evidence record, keyed by (pr, sha), from ONE ranked source.

    `base_tip` is the master tip this evidence was judged against (the
    merge-base the verified tree sat on), when the source knows it. A local
    verify records it; a Cloud Build check-run does not, and `None` means "the
    merged-tree seam decides at merge time" (lesson 3).
    """

    pr: int
    sha: str
    source: str
    state: str
    base_tip: str | None = None
    detail: str = ""
    recorded_at: str = ""

    @property
    def rank(self) -> int:
        return SOURCE_RANK.get(self.source, 99)


@dataclass(frozen=True)
class LiveBuild:
    """A build or verify the runner can see in flight (or just finished).

    `kind` is `cloud-build` or `local`; `status` uses the evidence vocabulary
    above (`pending` = running/queued, `expired`, `cancelled`, `parked`, ...).
    """

    id: str
    pr: int
    sha: str
    status: str
    kind: str = SOURCE_CLOUD_BUILD


@dataclass(frozen=True)
class PruneResult:
    """What `fleet/gatelock.py prune --apply` said before this cycle (lesson 7)."""

    ok: bool
    detail: str = ""


# --- actions -----------------------------------------------------------------
CANCEL_STALE = "cancel-stale"
HOLD = "hold"
MERGE = "merge"
VERIFY = "verify"
AWAIT = "await"
DEFER = "defer"
REFUSE = "refuse"
SKIP = "skip"

#: The order actions are emitted in: free capacity first (cancel the stale),
#: then what is blocked and why, then merges, then new verifies.
ACTION_ORDER: tuple[str, ...] = (CANCEL_STALE, HOLD, REFUSE, MERGE, VERIFY, AWAIT, DEFER, SKIP)


@dataclass(frozen=True)
class Action:
    """One planned step. `reason` is a kebab-case name with a `:<detail>` tail."""

    kind: str
    pr: int
    sha: str = ""
    reason: str = ""
    build_id: str = ""
    via: str = ""
    extra: dict = field(default_factory=dict, compare=False)

    def as_dict(self) -> dict:
        out = {"kind": self.kind, "pr": self.pr}
        if self.sha:
            out["sha"] = self.sha
        if self.reason:
            out["reason"] = self.reason
        if self.build_id:
            out["build_id"] = self.build_id
        if self.via:
            out["via"] = self.via
        return out
