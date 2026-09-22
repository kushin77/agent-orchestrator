"""evidence.py — ranked, per-(pr, sha) evidence for the PR runner (issue #1343).

---knowledge---
module_id: fleet.runner.evidence
system: fleet
app: fleet
solution_class: pattern
patterns: []
derives_from: null
owner_sme: unassigned
tier: L1
interfaces: [state_of_check_run, state_of_commit_status, state_of_cloud_build, state_of_verify_rc, Verdict, EvidenceTable, from_check_runs, from_commit_status, (+3 more)]
invariants: ""
gotchas: ""
related: []
do_not_duplicate: null
---knowledge---

THREE SOURCES, ONE RANK ORDER
    cloud-build   the `control-plane-verify` check-run for the exact head sha
    gate-status   the `ao/gate-of-record` commit status read back for the sha
    local-marker  `.fleet/runner/local-green/<pr>-<sha>` written by verify.py

    Lesson 5: a runner that cannot reach a precondition (gh auth, docker
    compose, AR creds, the auth-gate env) records CANNOT-ASSESS by name and is
    never counted green — and a host-env RED must not block a merge Cloud
    Build proved. So `verdict()` picks the best-ranked GREEN when any source is
    green, records the rest alongside it, and only falls through to the
    best-ranked non-green when nothing is green.

THE KEY IS (pr, sha), NEVER pr
    Lesson 1. `EvidenceTable.for_head()` returns only records for the sha
    asked about; a record for an older head is invisible to the planner.

This module is pure. The real readers (gh, gcloud, the marker directory) live
in `cli.py` and are injected.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from fleet.runner.model import (
    CANNOT_ASSESS,
    GREEN,
    RED,
    REQUEUE_STATES,
    SOURCE_CLOUD_BUILD,
    SOURCE_GATE_STATUS,
    SOURCE_LOCAL,
    Evidence,
)

# How each source's raw vocabulary maps onto the evidence states. Anything not
# in a table is CANNOT-ASSESS: an unknown word is never a green.
CHECK_RUN_CONCLUSIONS: dict[str, str] = {
    "success": GREEN,
    "failure": RED,
    "cancelled": "cancelled",
    "timed_out": "expired",
    "action_required": CANNOT_ASSESS,
    "neutral": CANNOT_ASSESS,
    "skipped": CANNOT_ASSESS,
    "stale": "expired",
}
COMMIT_STATUS_STATES: dict[str, str] = {
    "success": GREEN,
    "failure": RED,
    "error": CANNOT_ASSESS,
    "pending": "pending",
}
CLOUD_BUILD_STATUSES: dict[str, str] = {
    "SUCCESS": GREEN,
    "FAILURE": RED,
    "INTERNAL_ERROR": CANNOT_ASSESS,
    "TIMEOUT": "expired",
    "EXPIRED": "expired",
    "CANCELLED": "cancelled",
    "QUEUED": "pending",
    "WORKING": "pending",
    "PENDING": "pending",
}
#: `scripts/verify.sh` rc -> evidence state. 10/11 are PARKED (not a verdict).
VERIFY_RC_STATES: dict[int, str] = {0: GREEN, 1: RED, 2: CANNOT_ASSESS, 10: "parked", 11: "parked"}


def state_of_check_run(conclusion: str | None) -> str:
    if not conclusion:
        return "pending"
    return CHECK_RUN_CONCLUSIONS.get(conclusion.lower(), CANNOT_ASSESS)


def state_of_commit_status(state: str | None) -> str:
    return COMMIT_STATUS_STATES.get((state or "").lower(), CANNOT_ASSESS)


def state_of_cloud_build(status: str | None) -> str:
    return CLOUD_BUILD_STATUSES.get((status or "").upper(), CANNOT_ASSESS)


def state_of_verify_rc(rc: int) -> str:
    return VERIFY_RC_STATES.get(int(rc), CANNOT_ASSESS)


@dataclass(frozen=True)
class Verdict:
    """The runner's reading of every record for one (pr, sha)."""

    pr: int
    sha: str
    state: str  # GREEN / RED / a REQUEUE state / "none"
    basis: Evidence | None
    records: tuple[Evidence, ...]
    #: Records set aside because they name a `base_tip` that is not the CURRENT
    #: master tip (lesson 3): green for an older master is not green now.
    stale: tuple[Evidence, ...] = ()

    @property
    def green(self) -> bool:
        return self.state == GREEN

    @property
    def requeue(self) -> bool:
        return self.state in REQUEUE_STATES or self.state == "none"

    @property
    def has_local_record(self) -> bool:
        """Whether ANY record for this head came from the local marker,
        regardless of its state or whether it is the basis (issue #1378).

        Used to distinguish a foreign RED the runner has never itself
        verified (re-queue it) from one it already ran locally (stay
        refused, no point burning a slot re-running it)."""
        return any(r.source == SOURCE_LOCAL for r in self.records)

    def explain(self) -> str:
        if self.basis is None:
            return f"no evidence for #{self.pr}@{self.sha[:12]}"
        others = [f"{r.source}={r.state}" for r in self.records if r is not self.basis]
        others += [f"{r.source}={r.state}@{(r.base_tip or '')[:12]}:stale" for r in self.stale]
        tail = f" (also: {', '.join(others)})" if others else ""
        return f"{self.basis.source}={self.basis.state}{tail}"


class EvidenceTable:
    """Every evidence record the runner knows, queried by (pr, sha) only."""

    def __init__(self, records: list[Evidence] | tuple[Evidence, ...] = ()):
        self._records: list[Evidence] = list(records)

    def add(self, record: Evidence) -> None:
        self._records.append(record)

    def all(self) -> tuple[Evidence, ...]:
        return tuple(self._records)

    def for_head(self, pr: int, sha: str) -> tuple[Evidence, ...]:
        """Only the records for THIS head. An older head's records are invisible."""
        return tuple(r for r in self._records if r.pr == pr and r.sha == sha)

    def verdict(self, pr: int, sha: str, master_tip: str | None = None) -> Verdict:
        """The reading for one head, judged against the CURRENT master tip.

        A record whose `base_tip` is set and differs from `master_tip` is
        merged-tree evidence for an OLDER master: it is set aside as `stale`
        and never a basis (lesson 3). A record with no `base_tip` is head-level
        evidence; the merged-tree seam judges it at merge time.
        """
        every = sorted(self.for_head(pr, sha), key=lambda r: (r.rank, r.recorded_at))
        stale = tuple(r for r in every if r.base_tip is not None and master_tip is not None and r.base_tip != master_tip)
        records = [r for r in every if r not in stale]
        if not records:
            return Verdict(pr, sha, "none", None, (), stale)
        greens = [r for r in records if r.state == GREEN]
        if greens:
            return Verdict(pr, sha, GREEN, greens[0], tuple(records), stale)
        reds = [r for r in records if r.state == RED]
        if reds:
            return Verdict(pr, sha, RED, reds[0], tuple(records), stale)
        # Nothing finished with a verdict: the best-ranked record explains why.
        best = records[0]
        return Verdict(pr, sha, best.state, best, tuple(records), stale)


# --- builders for the real sources (pure: they take already-fetched payloads) --
def from_check_runs(pr: int, sha: str, payload: dict, *, name: str = "control-plane-verify") -> list[Evidence]:
    """Evidence from `GET /repos/{r}/commits/{sha}/check-runs` for the CB check."""
    out: list[Evidence] = []
    for run in payload.get("check_runs", []) or []:
        if name not in str(run.get("name", "")):
            continue
        out.append(
            Evidence(
                pr=pr,
                sha=sha,
                source=SOURCE_CLOUD_BUILD,
                state=state_of_check_run(run.get("conclusion")),
                detail=f"check-run {run.get('name')} {run.get('status')}/{run.get('conclusion')}",
                recorded_at=str(run.get("completed_at") or run.get("started_at") or ""),
            )
        )
    return out


def from_commit_status(pr: int, sha: str, payload: dict, *, context: str) -> list[Evidence]:
    """Evidence from `GET /repos/{r}/commits/{sha}/status` for our own context."""
    out: list[Evidence] = []
    for status in payload.get("statuses", []) or []:
        if status.get("context") != context:
            continue
        out.append(
            Evidence(
                pr=pr,
                sha=sha,
                source=SOURCE_GATE_STATUS,
                state=state_of_commit_status(status.get("state")),
                detail=str(status.get("description") or ""),
                recorded_at=str(status.get("updated_at") or ""),
            )
        )
        break  # GitHub lists newest first; one record per context
    return out


def from_local_markers(directory: Path) -> list[Evidence]:
    """Evidence from `.fleet/runner/local-green/<pr>-<sha>` marker files.

    Each marker is a small JSON document written by `verify.py` (rc, base_tip,
    recorded_at, failing_checks). A marker that cannot be parsed is
    CANNOT-ASSESS by name, not skipped: an unreadable record must never silently
    become "no evidence" — and that includes a `failing_checks` that is not a
    list of names, which is exactly the shape a reader would otherwise mistake
    for "the run named no failing check" (issue #1384).
    """
    out: list[Evidence] = []
    if not directory.is_dir():
        return out
    for path in sorted(directory.iterdir()):
        stem = path.name
        if "-" not in stem:
            continue
        pr_text, _, sha = stem.partition("-")
        if not pr_text.isdigit():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8") or "{}")
            rc = int(data["rc"])
            state = state_of_verify_rc(rc)
            detail = str(data.get("detail") or f"verify rc {rc}")
            base_tip = data.get("base_tip")
            recorded_at = str(data.get("recorded_at") or "")
            failing_checks = marker_failing_checks(data)
        except (OSError, ValueError, KeyError, TypeError):
            state, detail, base_tip, recorded_at, failing_checks = CANNOT_ASSESS, f"marker-unreadable:{stem}", None, "", ()
        out.append(
            Evidence(
                pr=int(pr_text),
                sha=sha,
                source=SOURCE_LOCAL,
                state=state,
                base_tip=base_tip,
                detail=detail,
                recorded_at=recorded_at,
                failing_checks=failing_checks,
            )
        )
    return out


def marker_failing_checks(data: dict) -> tuple[str, ...]:
    """The check names a marker recorded, or a REFUSAL when the shape is wrong.

    Absent is fine (a marker written before issue #1384, or a run that named
    nothing): it reads as the empty tuple, which is what `none-named` renders.
    Present-but-wrong is a `TypeError` so the caller's parse guard turns the
    whole record into CANNOT-ASSESS by name — the alternative (coercing it to
    "no failing checks") would launder a corrupt record into a plausible one.
    """
    raw = data.get("failing_checks")
    if raw is None:
        return ()
    if not isinstance(raw, list) or any(not isinstance(name, str) for name in raw):
        raise TypeError("failing_checks is not a list of check names")
    return tuple(raw)


def write_local_marker(
    directory: Path,
    pr: int,
    sha: str,
    rc: int,
    *,
    base_tip: str | None,
    detail: str,
    recorded_at: str,
    failing_checks: Iterable[str] = (),
    verify_summary: str = "",
    evidence_log: str = "",
) -> Path:
    """Record a local verify outcome. PARKED rcs are recorded too — as `parked`,
    which the planner re-queues (lesson 2) — never as a verdict.

    `failing_checks` is what the PLAN consumes (it becomes the red's name), while
    `verify_summary` (the gate's own `verify: FAIL (...)` line) and `evidence_log`
    (the kept transcript, `.fleet/runner/logs/<pr>-<sha>.log`) are carried for the
    reader: a red whose record is `{"rc": 1}` alone cannot be diagnosed or
    contested once the worktree is gone (issue #1384).
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{pr}-{sha}"
    path.write_text(
        json.dumps(
            {
                "rc": int(rc),
                "base_tip": base_tip,
                "detail": detail,
                "recorded_at": recorded_at,
                "failing_checks": [str(name) for name in failing_checks],
                "verify_summary": str(verify_summary),
                "evidence_log": str(evidence_log),
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path
