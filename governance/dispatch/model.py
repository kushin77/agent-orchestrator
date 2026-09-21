"""Data model for claim-time issue-order enforcement (issue #157).

---knowledge---
module_id: governance.dispatch.model
system: governance
app: dispatch
solution_class: enterprise
patterns: [provoked-negative-control, append-only-ledger, no-false-green, offline-hermetic, bounded-work]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [Issue, Snapshot, Eligibility, Provenance, Arbitration, FileClaim, regions_overlap, file_claims_conflict, ClaimEvent, parse_provenance, (+2 more)]
invariants: ""
gotchas: ""
related: ["#128", "#157", "#181", "#699", "#702", "#707"]
do_not_duplicate: null
---knowledge---

The chronological-dispatch rule (`AGENTS.md` golden rule 14,
`docs/GOVERNANCE.md` 8, `docs/EXECUTION-PLAN.md` 5) is declared in docs and
gated by `scripts/check-chronological-dispatch.sh`. That gate is a *declaration*
gate: it fails when the docs stop declaring the rule. This package is the
*behavioural* half — it decides, at claim time, whether an issue is the next
eligible step in the active dependency chain.

Everything here is stdlib-only and offline: the board state is a committed
snapshot (`.board/snapshot.json`) and claims are an append-only ledger
(`.board/claims.jsonl`), so the gate can audit real state without the network.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from governance.policy import lease  # noqa: E402

# Reasons that justify a claim. Anything else is kanban scavenging.
REASON_CHILD_OF_CLAIM = "child-of-claim"
REASON_SUCCESSOR_OF_CLAIM = "successor-of-claim"
REASON_NEXT_IN_MILESTONE = "next-in-milestone"
REASON_BRAIN_DIRECTED = "brain-directed"
# A child of the ACTIVE epic (epic focus, issue #707): stricter than the
# milestone frontier, it is a chain edge to the epic the fleet is driving.
REASON_ACTIVE_EPIC_CHILD = "active-epic-child"
# Branch-stacking (DG-3, issue #699, dispatch half): a claim that would
# otherwise be refused ONLY because it is blocked-by an in-flight upstream
# lane is accepted as SPECULATIVE when `claim --base <upstream-branch>` names
# that upstream's own branch. Never a chain edge on its own — it only ever
# substitutes for one when `--base` proves (by naming the exact branch of one
# of the issue's open blockers) which upstream it is speculating against, and
# it hands off to `governance.isolation.speculative.claim` so the isolation
# gate re-verifies before the lane's PR (see governance/isolation/README.md
# §7.1). A blocked issue with no matching `--base` is still `blocked`; a
# `--base` naming a branch that is not the blocking upstream's is refused by
# name (`speculative-base-not-upstream`), never silently accepted.
REASON_SPECULATIVE_BASE = "speculative_base"
ALLOWED_CLAIM_REASONS = (
    REASON_CHILD_OF_CLAIM,
    REASON_SUCCESSOR_OF_CLAIM,
    REASON_NEXT_IN_MILESTONE,
    REASON_BRAIN_DIRECTED,
    REASON_ACTIVE_EPIC_CHILD,
    REASON_SPECULATIVE_BASE,
)

# Reasons a claim is refused.
REASON_UNKNOWN_ISSUE = "unknown-issue"
REASON_ISSUE_CLOSED = "issue-closed"
REASON_EPIC_CLOSED = "epic-closed"
REASON_BLOCKED = "blocked"
REASON_ALREADY_CLAIMED = "already-claimed"
REASON_NO_CHAIN_EDGE = "no-chain-edge"
REASON_EPIC_NOT_WORKABLE = "epic-not-workable"
# Escalation refusal (issue #1851). An issue carrying an `escalate:*` label has
# already been through the tiered climb, so it is a terminal-ish state for the
# FRONTIER: advertising it names work no reader can take — the same shape as
# #1168's `epic-closed` refusal. `tiered.py` climbs tiers INSIDE one lane run
# and never reads the frontier (its own docstring: "the loop applies the
# ``escalate:*`` label and re-dispatches one tier up"), so skipping it here
# loses nothing and needs no new marker — the `escalate:*` label IS the marker.
REASON_ESCALATED = "escalated"
# Epic focus (#707): the issue is outside the active epic, so while a focus is
# active the fleet does not dispatch it. Distinct from `no-chain-edge` because
# the issue is not being rejected as scavenging — it is being WAITING, and it is
# parked in `.board/pool.jsonl` so it is never silently dropped.
REASON_OUT_OF_EPIC_POOLED = "out-of-epic-pooled"
# Provenance refusals (#726): an addressable unit of work must PROVE
# issue -> epic -> lane ownership before it is dispatched, so a unit that is
# already owned, mis-declared or unowned is refused rather than routed.
REASON_PROVENANCE_MISMATCH = "provenance-mismatch"
REASON_UNOWNED = "unowned"
# Not an ownership refusal: the board evidence is too old to judge with at all.
REASON_SNAPSHOT_STALE = "snapshot-stale"
# Per-file leases (issue #702): a claim may name the files it touches, with an
# optional region per file. Two live claims naming the SAME file with
# OVERLAPPING regions (or either with no region — the whole file) conflict; two
# claims naming the same file with disjoint regions do not, so a slow neighbour
# touching a different region of one file no longer serializes the whole file.
REASON_FILE_REGION_CLAIMED = "file-region-claimed"
# Branch-stacking (#699 dispatch half): `--base <branch>` was given but does not
# name the branch of one of the issue's own open blockers — never the
# out-of-order reason itself (that stays `blocked`, `no-chain-edge`,
# `already-claimed`, etc., all still refused with `--base` present), only the
# separate claim that the speculative exemption was invoked against the wrong
# upstream.
REASON_SPECULATIVE_BASE_NOT_UPSTREAM = "speculative-base-not-upstream"
# `--base` named the right upstream, but the isolation-side attestation itself
# could not be recorded (unresolvable ref, unmeasurable git state) — GR-12:
# unproven is never a pass.
REASON_SPECULATIVE_CLAIM_FAILED = "speculative-claim-failed"

#: Every reason the A2A arbitration (`claims.arbitrate`) can refuse with. Its
#: self-control must provoke all of them or the gate fails, so a refusal cannot
#: be added without a control that proves it bites (GR-12 / AO-GR-19).
ARBITRATION_REFUSALS = (
    REASON_UNKNOWN_ISSUE,
    REASON_ISSUE_CLOSED,
    REASON_EPIC_CLOSED,
    REASON_EPIC_NOT_WORKABLE,
    REASON_BLOCKED,
    REASON_ALREADY_CLAIMED,
    REASON_PROVENANCE_MISMATCH,
    REASON_UNOWNED,
    REASON_SNAPSHOT_STALE,
)

#: Refusals that are terminal BY DEFINITION (issue #861): the claim is refused
#: for a reason no retry can ever cure — the issue is closed, its epic is closed,
#: or the unit has no owning lane. These are distinct from a transient refusal
#: (``already-claimed``, ``blocked``, ``snapshot-stale``) that a retry, a
#: self-heal or a board refresh can plausibly resolve. A directive refused for
#: one of these reasons is dead on arrival: the fleet's runaway guard (#723)
#: reads this set to dead-letter it on the FIRST refusal instead of spending K
#: attempts (30/60/120/240s of a held queue slot) discovering what this set
#: already knows.
TERMINAL_CLAIM_REASONS = (
    REASON_ISSUE_CLOSED,
    REASON_EPIC_CLOSED,
    REASON_UNOWNED,
)

CLAIM_EVENTS = ("claim", "release", "take-over", "reap")

MISSING = object()


@dataclass(frozen=True)
class Issue:
    """One issue as recorded in the board snapshot."""

    number: int
    title: str = ""
    state: str = "open"
    milestone: str = ""
    labels: tuple[str, ...] = ()
    parent: int | None = None
    blocked_by: tuple[int, ...] = ()
    # Cross-repo chain edges, e.g. ``kushin77/code-indexing#128`` (issue #181).
    # A foreign board is not in this snapshot, so these are captured but never
    # gated by the same-repo eligibility rules — they are surfaced for the
    # wave-bootstrap report and the triage lane to act on.
    cross_refs: tuple[str, ...] = ()
    closed_at: str = ""
    # Declared file ownership (issue #740, dispatch half): the `Files: a, b` /
    # `Files owned (disjoint): …` convention in the issue body. Empty means the
    # issue declared no files — UNVERIFIABLE for the collision check, never the
    # same as "declared to own nothing" (mirrors `capacity.declared_files`).
    files: tuple[str, ...] = ()

    @property
    def closed(self) -> bool:
        return self.state.strip().lower() == "closed"

    @property
    def is_epic(self) -> bool:
        """An epic is closed by its children; it is never a unit of work."""
        return "type:epic" in self.labels

    def to_json(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "title": self.title,
            "state": self.state,
            "milestone": self.milestone,
            "labels": list(self.labels),
            "parent": self.parent,
            "blocked_by": list(self.blocked_by),
            "cross_refs": list(self.cross_refs),
            "closed_at": self.closed_at,
            "files": list(self.files),
        }


@dataclass(frozen=True)
class Snapshot:
    """The board state a claim is validated against."""

    generated_at: str
    source: str
    issues: dict[int, Issue] = field(default_factory=dict)

    def get(self, number: int) -> Issue | None:
        return self.issues.get(number)

    def open_issues(self) -> list[Issue]:
        return [i for i in self.issues.values() if not i.closed]

    def blockers_open(self, issue: Issue) -> list[int]:
        """Blocker numbers that are still open (unknown blockers count as open)."""
        open_blockers = []
        for number in issue.blocked_by:
            blocker = self.issues.get(number)
            if blocker is None or not blocker.closed:
                open_blockers.append(number)
        return sorted(open_blockers)

    def children_of(self, number: int) -> list[Issue]:
        return sorted(
            (i for i in self.issues.values() if i.parent == number),
            key=lambda i: i.number,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "source": self.source,
            "issues": [i.to_json() for i in sorted(self.issues.values(), key=lambda i: i.number)],
        }


@dataclass(frozen=True)
class Eligibility:
    """The verdict for one (issue, agent) pair."""

    issue: int
    eligible: bool
    reason: str
    detail: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "issue": self.issue,
            "eligible": self.eligible,
            "reason": self.reason,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class Provenance:
    """Who owns a unit of work: issue -> epic -> lane, plus the evidence checked.

    Recorded on the claim that took the unit (#726) and printed by the
    ``dispatch`` arbitration, so a reader can see *why* the owner is the owner.
    ``evidence`` names the artifacts the arbitration actually read (the board
    snapshot it judged against, the directive envelope, the ledger record), which
    is what a refusal quotes back.
    """

    issue: int
    epic: int | None = None
    lane: str = ""
    evidence: tuple[str, ...] = ()

    @property
    def owned(self) -> bool:
        """Whether a lane owns the unit — an unowned unit is never dispatchable."""
        return bool(self.lane.strip())

    def to_json(self) -> dict[str, Any]:
        return {
            "issue": self.issue,
            "epic": self.epic,
            "lane": self.lane,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class Arbitration:
    """The verdict of an A2A dispatch arbitration (issue #726).

    A granted verdict is the proof that was missing: the unit's issue, the epic
    it belongs to, and the lane that owns it — each resolved from named evidence
    rather than assumed from the directive's own say-so.
    """

    issue: int
    agent: str
    lane: str
    epic: int | None
    directive_id: str
    provenance: Provenance

    def to_json(self) -> dict[str, Any]:
        return {
            "verdict": "granted",
            "issue": self.issue,
            "agent": self.agent,
            "lane": self.lane,
            "epic": self.epic,
            "directive_id": self.directive_id,
            "provenance": self.provenance.to_json(),
        }


Region = tuple[int, int]


@dataclass(frozen=True)
class FileClaim:
    """One file named by a claim, with an optional list of line regions.

    ``regions is None`` means the whole file is held (the pre-#702 behaviour).
    A non-empty ``regions`` names the ``[start, end]`` (inclusive) line spans the
    claim actually touches, so a second claim on the SAME file with DISJOINT
    regions does not conflict.
    """

    path: str
    regions: tuple[Region, ...] | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "regions": [list(region) for region in self.regions] if self.regions is not None else None,
        }


def regions_overlap(a: Region | None, b: Region | None) -> bool:
    """True when two (inclusive) line regions overlap.

    ``None`` means "the whole file" and overlaps every region, including
    another ``None``. This is the single predicate both the claim path and the
    reap/reconcile provocation exercise — it must never be re-declared.
    """
    if a is None or b is None:
        return True
    a_start, a_end = a
    b_start, b_end = b
    return a_start <= b_end and b_start <= a_end


def file_claims_conflict(a: FileClaim, b: FileClaim) -> bool:
    """True when two ``FileClaim`` records name the same path with overlap."""
    if a.path != b.path:
        return False
    if a.regions is None or b.regions is None:
        return True
    return any(regions_overlap(ra, rb) for ra in a.regions for rb in b.regions)


@dataclass(frozen=True)
class ClaimEvent:
    """One line of the append-only claim ledger."""

    event: str
    issue: int
    agent: str
    at: str
    lane: str = ""
    base_commit: str = ""
    snapshot_sha256: str = ""
    reason: str = ""
    # The claim lease is declared once in governance/policy/lease.py.
    ttl_hours: int = lease.CLAIM_TTL_HOURS
    directive_id: str = ""
    directive_from: str = ""
    reaped_agent: str = ""
    #: issue -> epic -> lane ownership, arbitrated before the claim was recorded.
    provenance: Provenance | None = None
    #: Per-file leases (#702). Empty means the claim declared no files (the
    #: pre-#702 shape) — whole-issue exclusivity still governs it.
    files: tuple[FileClaim, ...] = ()

    @property
    def is_claim(self) -> bool:
        return self.event in ("claim", "take-over")

    def to_json(self) -> dict[str, Any]:
        payload = {
            "event": self.event,
            "issue": self.issue,
            "agent": self.agent,
            "at": self.at,
            "lane": self.lane,
            "base_commit": self.base_commit,
            "snapshot_sha256": self.snapshot_sha256,
            "reason": self.reason,
            "ttl_hours": self.ttl_hours,
            "directive_id": self.directive_id,
            "directive_from": self.directive_from,
            "reaped_agent": self.reaped_agent,
        }
        # Provenance belongs to the claim that took the unit, not to the
        # release/reap that ended it, so the key is only written when it was
        # arbitrated (a release record keeps its pre-#726 shape).
        if self.provenance is not None:
            payload["provenance"] = self.provenance.to_json()
        if self.files:
            payload["files"] = [f.to_json() for f in self.files]
        return payload


def _require(obj: Any, key: str, kind: type, where: str) -> Any:
    value = obj.get(key, MISSING)
    if value is MISSING:
        raise ValueError(f"{where}: missing required field '{key}'")
    if kind is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{where}: field '{key}' must be an integer, got {type(value).__name__}")
    elif not isinstance(value, kind):
        raise ValueError(f"{where}: field '{key}' must be {kind.__name__}, got {type(value).__name__}")
    return value


def parse_provenance(obj: Any, where: str = "ledger") -> Provenance:
    """Parse and validate a recorded provenance. Raises ValueError with the reason."""
    if not isinstance(obj, dict):
        raise ValueError(f"{where}: field 'provenance' must be a JSON object")
    issue = _require(obj, "issue", int, where)
    epic = obj.get("epic")
    if epic is not None and (isinstance(epic, bool) or not isinstance(epic, int)):
        raise ValueError(f"{where}: field 'provenance.epic' must be an integer or null")
    lane = obj.get("lane", "")
    if not isinstance(lane, str):
        raise ValueError(f"{where}: field 'provenance.lane' must be a string")
    evidence = obj.get("evidence") or []
    if not isinstance(evidence, list) or not all(isinstance(item, str) for item in evidence):
        raise ValueError(f"{where}: field 'provenance.evidence' must be a list of strings")
    return Provenance(issue=issue, epic=epic, lane=lane, evidence=tuple(evidence))


def parse_claim_event(obj: Any, where: str = "ledger") -> ClaimEvent:
    """Parse and validate one ledger record. Raises ValueError with the reason."""
    if not isinstance(obj, dict):
        raise ValueError(f"{where}: record must be a JSON object")
    event = _require(obj, "event", str, where)
    if event not in CLAIM_EVENTS:
        raise ValueError(f"{where}: unknown event '{event}' (expected one of {', '.join(CLAIM_EVENTS)})")
    issue = _require(obj, "issue", int, where)
    agent = _require(obj, "agent", str, where)
    at = _require(obj, "at", str, where)
    if not agent.strip():
        raise ValueError(f"{where}: field 'agent' must not be empty")
    ttl = obj.get("ttl_hours", lease.CLAIM_TTL_HOURS)
    if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl <= 0:
        raise ValueError(f"{where}: field 'ttl_hours' must be a positive integer")
    recorded = obj.get("provenance")
    provenance = parse_provenance(recorded, where=where) if recorded is not None else None
    files = parse_file_claims(obj.get("files"), where=where)
    return ClaimEvent(
        event=event,
        issue=issue,
        agent=agent.strip(),
        at=at.strip(),
        lane=str(obj.get("lane", "") or ""),
        base_commit=str(obj.get("base_commit", "") or ""),
        snapshot_sha256=str(obj.get("snapshot_sha256", "") or ""),
        reason=str(obj.get("reason", "") or ""),
        ttl_hours=ttl,
        directive_id=str(obj.get("directive_id", "") or ""),
        directive_from=str(obj.get("directive_from", "") or ""),
        reaped_agent=str(obj.get("reaped_agent", "") or ""),
        provenance=provenance,
        files=files,
    )


def parse_file_claims(obj: Any, where: str = "ledger") -> tuple[FileClaim, ...]:
    """Parse the ``files`` field of a ledger record. Raises ValueError."""
    if obj is None:
        return ()
    if not isinstance(obj, list):
        raise ValueError(f"{where}: field 'files' must be a list")
    result: list[FileClaim] = []
    for i, item in enumerate(obj):
        item_where = f"{where}.files[{i}]"
        if not isinstance(item, dict):
            raise ValueError(f"{item_where}: must be a JSON object")
        path = _require(item, "path", str, item_where)
        if not path.strip():
            raise ValueError(f"{item_where}: field 'path' must not be empty")
        raw_regions = item.get("regions")
        regions: tuple[Region, ...] | None
        if raw_regions is None:
            regions = None
        else:
            if not isinstance(raw_regions, list):
                raise ValueError(f"{item_where}: field 'regions' must be a list or null")
            parsed_regions: list[Region] = []
            for j, region in enumerate(raw_regions):
                region_where = f"{item_where}.regions[{j}]"
                if (
                    not isinstance(region, list)
                    or len(region) != 2
                    or any(isinstance(x, bool) or not isinstance(x, int) for x in region)
                ):
                    raise ValueError(f"{region_where}: must be a [start, end] pair of integers")
                start, end = region
                if start > end:
                    raise ValueError(f"{region_where}: start ({start}) must be <= end ({end})")
                parsed_regions.append((start, end))
            regions = tuple(parsed_regions)
        result.append(FileClaim(path=path.strip(), regions=regions))
    return tuple(result)
