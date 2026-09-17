#!/usr/bin/env python3
"""Unified fleet-state projection across lanes, sessions, claims and journals (#323).

The fleet keeps one work item's state in five different stores, each added by a
different lane: lane identities in ``.fleet/lanes/``, session heartbeats in
``.fleet/sessions/``, closure journals in ``.fleet/lifecycle/``, claims in
``.board/claims/`` and authorisation directives in ``.fleet/{sent,done}/``. No
one place answered "what is in flight, what is orphaned, what is shelved, what is
wedged" — the principal reconstructed it by shelling into three CLIs, reading a
ledger and listing worktrees by hand, which is how an orphaned lane survived
hours. ``fleet/console.py`` is a dashboard for the *rungs only*; it joins none of
these.

This module is that one place. It is **read-only**: it opens the stores, joins
them by issue number, and prints the projection. It never writes, moves or
reclaims anything — the destructive half deliberately lives in
``governance/reconcile`` and this module must not duplicate it.

Two properties are load-bearing and are what the gate proves:

* **Derived, never a copy.** Every field is recomputed from the stores on every
  invocation, so no snapshot can go stale. Where two stores describe the same
  fact — the lane record's branch and the heartbeat's branch, say — the
  projection does **not** pick a winner: it compares them and reports a mismatch.
  A second store cannot disagree with the first without the projection showing
  it (``lane-session-mismatch``).
* **An exit code a cron can use.** Orphaned, shelved and wedged items make the
  command exit non-zero, so the same invocation is both the human view and a
  health input. The tri-state follows the repo convention: 0 OK / 1 NOT-OK /
  2 CANNOT-ASSESS.

Claim store note: the authoritative claim store is the one-file-per-event
directory ``.board/claims/`` (``governance/dispatch/claims.py``). The legacy
single-file ledger ``.board/claims.jsonl`` is *frozen history* and is read only
because ``claims.read_ledger`` merges it first so a replay stays time-ordered —
ignoring it would silently drop every claim written before issue #170.

Usage::

    python3 fleet/state.py                 # human view, exit 1 if not clean
    python3 fleet/state.py --json          # the same projection, machine-readable
    python3 fleet/state.py --root <dir>    # project a fixture tree (gate/tests)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
_HERE = str(Path(__file__).resolve().parent)
for _entry in (_HERE, str(ROOT), str(ROOT / "governance" / "dispatch")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from governance.reconcile.heartbeat import (  # noqa: E402
    DEFAULT_TTL_MINUTES,
    LIVE,
    ORPHAN,
    SHELVED,
    SUSPECT,
    Session,
    judge,
)

EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2

#: A session status the projection prints. ``absent`` is not a session verdict —
#: it means no store recorded a beat at all, which is itself a fact worth seeing.
ABSENT = "absent"

#: Statuses that make the projection exit non-zero on their own.
BLOCKING_STATUSES = frozenset({ORPHAN, SHELVED})

# Finding codes. Every one is a real disagreement between two stores, or an
# artifact stranded by the lifecycle; each is printed with the item that owns it.
F_CLAIM_WITHOUT_LANE = "claim-without-lane"
F_LANE_WITHOUT_HEARTBEAT = "lane-without-heartbeat"
F_ORPHAN_LANE_RETAINED = "orphan-lane-retained"
F_SHELVED_WITHOUT_CLAIM = "shelved-without-claim"
F_LANE_SESSION_MISMATCH = "lane-session-mismatch"
F_CLAIM_AGENT_MISMATCH = "claim-agent-mismatch"
F_DIRECTIVE_CONSUMED_CLAIM_HELD = "directive-consumed-claim-held"
F_JOURNAL_CLOSED_CLAIM_HELD = "journal-closed-claim-held"
F_DIRECTIVE_MISSING = "directive-missing"
F_CLAIM_EXPIRED_WITH_LANE = "claim-expired-with-lane"

#: The closed vocabulary of findings, so a caller cannot invent a class the gate
#: has not been taught to exercise. A record that cannot be parsed is reported
#: separately (``Projection.unreadable``) since it belongs to no single item.
FINDING_CODES = frozenset(
    {
        F_CLAIM_WITHOUT_LANE,
        F_LANE_WITHOUT_HEARTBEAT,
        F_ORPHAN_LANE_RETAINED,
        F_SHELVED_WITHOUT_CLAIM,
        F_LANE_SESSION_MISMATCH,
        F_CLAIM_AGENT_MISMATCH,
        F_DIRECTIVE_CONSUMED_CLAIM_HELD,
        F_JOURNAL_CLOSED_CLAIM_HELD,
        F_DIRECTIVE_MISSING,
        F_CLAIM_EXPIRED_WITH_LANE,
    }
)


@dataclass(frozen=True)
class Finding:
    """One disagreement or stranded artifact, named by a closed code."""

    code: str
    detail: str

    def __post_init__(self) -> None:
        if self.code not in FINDING_CODES:
            raise ValueError(f"unknown finding code {self.code!r}")

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}" if self.detail else self.code


@dataclass(frozen=True)
class Directive:
    """An authorisation directive, and whether it has been consumed."""

    id: str
    state: str  # "sent" or "done"
    issue: int
    sender: str
    recipient: str


@dataclass(frozen=True)
class Claim:
    """The claim that holds (or held) an issue, replayed from the ledger."""

    issue: int
    agent: str
    lane: str
    at: str
    directive_id: str
    expired: bool


@dataclass
class Item:
    """One work item, joined across every store that knows about it."""

    issue: int
    lane: dict[str, Any] | None = None
    session: Session | None = None
    status: str = ABSENT
    reason: str = ""
    claim: Claim | None = None
    directive: Directive | None = None
    journal: dict[str, Any] | None = None
    findings: list[Finding] = field(default_factory=list)

    @property
    def blocking(self) -> bool:
        """Whether this item alone should make the projection exit non-zero."""
        return self.status in BLOCKING_STATUSES or bool(self.findings)

    def to_json(self) -> dict[str, Any]:
        return {
            "issue": self.issue,
            "lane": self.lane,
            "session_status": self.status,
            "session_reason": self.reason,
            "session": self.session.to_json() if self.session else None,
            "claim": (
                {
                    "agent": self.claim.agent,
                    "lane": self.claim.lane,
                    "at": self.claim.at,
                    "directive_id": self.claim.directive_id,
                    "expired": self.claim.expired,
                }
                if self.claim
                else None
            ),
            "directive": (
                {
                    "id": self.directive.id,
                    "state": self.directive.state,
                    "from": self.directive.sender,
                    "to": self.directive.recipient,
                }
                if self.directive
                else None
            ),
            "journal": (
                {
                    "closing_evidence": bool(self.journal.get("closing_evidence")),
                    "verify_ok": bool((self.journal.get("verify") or {}).get("ok")),
                    "verify_commit": str((self.journal.get("verify") or {}).get("commit") or ""),
                }
                if self.journal
                else None
            ),
            "findings": [{"code": f.code, "detail": f.detail} for f in self.findings],
            "blocking": self.blocking,
        }


@dataclass
class Projection:
    """Every work item's derived state for one invocation."""

    items: list[Item] = field(default_factory=list)
    at: float = 0.0
    ttl_minutes: float = DEFAULT_TTL_MINUTES
    unreadable: list[str] = field(default_factory=list)

    @property
    def by_status(self) -> dict[str, int]:
        counts = {LIVE: 0, SUSPECT: 0, ORPHAN: 0, SHELVED: 0, ABSENT: 0}
        for item in self.items:
            counts[item.status] = counts.get(item.status, 0) + 1
        return counts

    @property
    def wedged(self) -> list[Item]:
        return [item for item in self.items if item.findings]

    @property
    def blocking(self) -> list[Item]:
        return [item for item in self.items if item.blocking]

    @property
    def clean(self) -> bool:
        # A store the projection cannot read is a store it cannot attest, so an
        # unreadable record blocks rather than reading as an all-clear.
        return not self.blocking and not self.unreadable

    def to_json(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "ttl_minutes": self.ttl_minutes,
            "counts": self.by_status,
            "items": [item.to_json() for item in self.items],
            "wedged": [item.issue for item in self.wedged],
            "blocking": [item.issue for item in self.blocking],
            "unreadable": list(self.unreadable),
        }


# --------------------------------------------------------------------------
# store readers — each returns plain data; nothing here writes
# --------------------------------------------------------------------------


def load_json(path: Path, unreadable: list[str] | None = None) -> Any:
    """Parse a JSON file, recording (never raising on) an unreadable one.

    An unreadable store must be *visible*, not fatal: the projection's job is to
    report the fleet's state, and "this record could not be parsed" is part of
    that state.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        if unreadable is not None:
            unreadable.append(str(path))
        return None


def read_lanes(fleet_dir: Path, unreadable: list[str] | None = None) -> dict[int, dict[str, Any]]:
    """Lane identities keyed by issue, from ``<fleet>/lanes/*.json``."""
    lanes: dict[int, dict[str, Any]] = {}
    directory = fleet_dir / "lanes"
    if not directory.is_dir():
        return lanes
    for path in sorted(directory.glob("*.json")):
        payload = load_json(path, unreadable)
        if not isinstance(payload, dict):
            continue
        try:
            issue = int(payload.get("issue") or 0)
        except (TypeError, ValueError):
            continue
        if issue:
            lanes[issue] = payload
    return lanes


def read_sessions(sessions_dir: Path, unreadable: list[str] | None = None) -> dict[int, Session]:
    """Session heartbeats keyed by issue, from ``<fleet>/sessions/*.json``."""
    sessions: dict[int, Session] = {}
    if not sessions_dir.is_dir():
        return sessions
    for path in sorted(sessions_dir.glob("*.json")):
        payload = load_json(path, unreadable)
        if not isinstance(payload, dict):
            continue
        try:
            session = Session.from_json(payload)
        except (KeyError, TypeError, ValueError):
            if unreadable is not None:
                unreadable.append(str(path))
            continue
        if session.issue:
            sessions[session.issue] = session
    return sessions


def read_journals(lifecycle_dir: Path, unreadable: list[str] | None = None) -> dict[int, dict[str, Any]]:
    """Closure journals keyed by issue, from ``<fleet>/lifecycle/<issue>.json``."""
    journals: dict[int, dict[str, Any]] = {}
    if not lifecycle_dir.is_dir():
        return journals
    for path in sorted(lifecycle_dir.glob("*.json")):
        payload = load_json(path, unreadable)
        if isinstance(payload, dict):
            try:
                journals[int(path.stem)] = payload
            except ValueError:
                continue
    return journals


def read_claims(board_dir: Path, unreadable: list[str] | None = None) -> dict[int, Claim]:
    """The claim that holds each issue, replayed from the authoritative ledger.

    ``read_ledger`` merges the frozen legacy ``.board/claims.jsonl`` with the
    one-file-per-event directory ``.board/claims/``; both are needed for a
    time-ordered replay. ``replay`` returns the last claim per issue ignoring the
    TTL, and ``active_claims`` drops the ones past it — the difference is exactly
    "a claim exists but has expired", which the projection reports rather than
    hides.
    """
    try:
        import claims as dispatch_claims  # noqa: PLC0415 - optional at import time
    except ImportError:
        return {}
    try:
        events = dispatch_claims.read_ledger(board_dir / "claims")
        replayed = dispatch_claims.replay(events)
        active = dispatch_claims.active_claims(events)
    except (OSError, ValueError):
        # A corrupt ledger is absence of evidence, and that absence must be
        # visible rather than silently reading as "no claims held".
        if unreadable is not None:
            unreadable.append(str(board_dir / "claims"))
        return {}
    out: dict[int, Claim] = {}
    for issue, event in replayed.items():
        out[issue] = Claim(
            issue=issue,
            agent=str(event.agent),
            lane=str(event.lane),
            at=str(event.at),
            directive_id=str(event.directive_id),
            expired=issue not in active,
        )
    return out


def read_directives(fleet_dir: Path, unreadable: list[str] | None = None) -> dict[str, Directive]:
    """Authorisation directives keyed by id, from ``<fleet>/sent`` and ``done``.

    A directive is *consumed* when a file of the same name exists under ``done``
    — the same rule ``governance/lifecycle`` uses, so the two cannot drift.
    """
    sent_dir = fleet_dir / "sent"
    done_dir = fleet_dir / "done"
    directives: dict[str, Directive] = {}

    def build(path: Path, state: str) -> None:
        payload = load_json(path, unreadable)
        if not isinstance(payload, dict):
            return
        ident = str(payload.get("id") or path.stem)
        try:
            issue = int((payload.get("task") or {}).get("issue") or 0)
        except (TypeError, ValueError):
            issue = 0
        directives[ident] = Directive(
            id=ident,
            state=state,
            issue=issue,
            sender=str(payload.get("from") or ""),
            recipient=str(payload.get("to") or ""),
        )

    if sent_dir.is_dir():
        for path in sorted(sent_dir.glob("*.json")):
            build(path, "done" if (done_dir / path.name).exists() else "sent")
    if done_dir.is_dir():
        for path in sorted(done_dir.glob("*.json")):
            payload = load_json(path, unreadable)
            if not isinstance(payload, dict):
                continue
            ident = str(payload.get("id") or path.stem)
            if ident not in directives:
                build(path, "done")
    return directives


# --------------------------------------------------------------------------
# the projection — the join
# --------------------------------------------------------------------------


def _mismatch(field_name: str, lane_value: str, session_value: str) -> Finding:
    return Finding(
        F_LANE_SESSION_MISMATCH,
        f"{field_name}: lane={lane_value or '(empty)'} session={session_value or '(empty)'}",
    )


def _join_item(
    issue: int,
    *,
    lane: dict[str, Any] | None,
    session: Session | None,
    claim: Claim | None,
    directives: dict[str, Directive],
    journal: dict[str, Any] | None,
    moment: float,
    ttl_minutes: float,
    alive: dict[str, bool] | None,
) -> Item:
    """Derive one item's state, recording every cross-store disagreement."""
    item = Item(issue=issue, lane=lane, session=session, claim=claim, journal=journal)
    findings: list[Finding] = []

    # --- session status: the heartbeat's verdict, or "shelved" when declared ---
    if session is not None:
        if session.state == SHELVED:
            item.status = SHELVED
            item.reason = session.note or "declared shelved by the reconciliation worker"
        else:
            verdict = judge(
                session,
                ttl_minutes,
                at=moment,
                alive=(alive or {}).get(session.session_id) if alive else None,
            )
            item.status = verdict.status
            item.reason = verdict.reason
    else:
        item.status = ABSENT
        item.reason = "no session heartbeat on record"

    # --- directive: linked through the claim, else the directive's own issue ---
    directive: Directive | None = None
    if claim is not None and claim.directive_id:
        directive = directives.get(claim.directive_id)
        if directive is None:
            findings.append(
                Finding(
                    F_DIRECTIVE_MISSING,
                    f"claim cites directive {claim.directive_id!r}; no such file in sent/ or done/",
                )
            )
    if directive is None:
        for candidate in directives.values():
            if candidate.issue == issue:
                directive = candidate
                break
    item.directive = directive

    # --- cross-store disagreements (the "never a copy" property) --------------
    if lane is not None and session is not None:
        if str(lane.get("session_id") or "") != session.session_id:
            findings.append(
                _mismatch("session_id", str(lane.get("session_id") or ""), session.session_id)
            )
        if str(lane.get("branch") or "") != (session.branch or ""):
            findings.append(_mismatch("branch", str(lane.get("branch") or ""), session.branch))
        if str(lane.get("worktree") or "") != (session.worktree or ""):
            findings.append(_mismatch("worktree", str(lane.get("worktree") or ""), session.worktree))
        lane_agent = str(lane.get("agent_id") or "")
        if lane_agent != (session.agent or ""):
            findings.append(_mismatch("agent", lane_agent, session.agent or ""))

    if claim is not None and lane is not None:
        lane_agent = str(lane.get("agent_id") or "")
        if claim.agent and lane_agent and claim.agent != lane_agent:
            findings.append(
                Finding(
                    F_CLAIM_AGENT_MISMATCH,
                    f"claim={claim.agent} lane={lane_agent}",
                )
            )
    elif claim is not None and session is not None:
        if claim.agent and session.agent and claim.agent != session.agent:
            findings.append(
                Finding(
                    F_CLAIM_AGENT_MISMATCH,
                    f"claim={claim.agent} session={session.agent}",
                )
            )

    # --- stranded / wedged artifacts ------------------------------------------
    if claim is not None and lane is None and session is None:
        findings.append(
            Finding(F_CLAIM_WITHOUT_LANE, f"claim held by {claim.agent or '(unknown)'}, no lane, no heartbeat")
        )
    if lane is not None and session is None:
        findings.append(
            Finding(F_LANE_WITHOUT_HEARTBEAT, f"lane {lane.get('lane') or '(unnamed)'} has no session heartbeat")
        )
    if session is not None and session.state == SHELVED and claim is None:
        findings.append(Finding(F_SHELVED_WITHOUT_CLAIM, "shelved lane holds no claim; its work is unprotected"))
    if item.status == ORPHAN and lane is not None:
        findings.append(
            Finding(F_ORPHAN_LANE_RETAINED, f"orphaned lane {lane.get('lane') or '(unnamed)'} is still provisioned")
        )
    if claim is not None and claim.expired and lane is not None:
        findings.append(
            Finding(F_CLAIM_EXPIRED_WITH_LANE, f"claim by {claim.agent or '(unknown)'} expired past its TTL (at {claim.at})")
        )
    if claim is not None and directive is not None and directive.state == "done":
        findings.append(
            Finding(
                F_DIRECTIVE_CONSUMED_CLAIM_HELD,
                f"directive {directive.id} consumed but {claim.agent or '(unknown)'} still holds the issue",
            )
        )
    if claim is not None and journal is not None and bool(journal.get("closing_evidence")):
        findings.append(
            Finding(
                F_JOURNAL_CLOSED_CLAIM_HELD,
                f"closure journal carries evidence but {claim.agent or '(unknown)'} still holds the issue",
            )
        )

    item.findings = findings
    return item


def project(
    *,
    root: Path | str | None = None,
    fleet_dir: Path | str | None = None,
    board_dir: Path | str | None = None,
    ttl_minutes: float = DEFAULT_TTL_MINUTES,
    at: float | None = None,
    alive: dict[str, bool] | None = None,
) -> Projection:
    """Join the five stores into one projection. Read-only.

    ``alive`` overrides pid liveness per session id — the seam that makes
    "live vs suspect" testable without depending on real processes.
    """
    root_path = Path(root).resolve() if root is not None else ROOT
    if fleet_dir is not None:
        fleet_path = Path(fleet_dir)
    elif os.environ.get("AO_FLEET_DIR"):
        # The namespaced runtime dir (fleet/runtime.py): honoured so a second
        # fleet projecting its own state does not read the first fleet's.
        fleet_path = Path(os.environ["AO_FLEET_DIR"])
    else:
        fleet_path = root_path / ".fleet"
    board_path = Path(board_dir) if board_dir is not None else root_path / ".board"

    moment = time.time() if at is None else at
    unreadable: list[str] = []

    lanes = read_lanes(fleet_path, unreadable)
    sessions = read_sessions(fleet_path / "sessions", unreadable)
    journals = read_journals(fleet_path / "lifecycle", unreadable)
    claims = read_claims(board_path, unreadable)
    directives = read_directives(fleet_path, unreadable)

    # A directive that has been consumed and left no other artifact is history,
    # not in-flight work; an *unconsumed* directive names a live work item even
    # before a lane exists for it. Items with a lane, a beat, a journal or a
    # claim are in scope regardless.
    pending_directive_issues = {d.issue for d in directives.values() if d.issue and d.state == "sent"}
    issues = sorted(
        set(lanes) | set(sessions) | set(journals) | set(claims) | pending_directive_issues
    )
    items = [
        _join_item(
            issue,
            lane=lanes.get(issue),
            session=sessions.get(issue),
            claim=claims.get(issue),
            directives=directives,
            journal=journals.get(issue),
            moment=moment,
            ttl_minutes=ttl_minutes,
            alive=alive,
        )
        for issue in issues
    ]
    return Projection(items=items, at=moment, ttl_minutes=ttl_minutes, unreadable=unreadable)


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def render_text(projection: Projection) -> str:
    """The human view: one row per item, then the findings that back it."""
    lines = [
        f"fleet-state: {len(projection.items)} item(s) across lanes, sessions, claims, "
        f"journals and directives (ttl {projection.ttl_minutes:g}m)",
        "",
        f"{'ISSUE':>6}  {'LANE':<18} {'SESSION':<14} {'AGENT':<20} "
        f"{'STATUS':<8} {'CLAIM':<20} {'DIRECTIVE':<10} FINDINGS",
    ]
    for item in projection.items:
        lane_label = ""
        if item.lane is not None:
            lane_label = str(item.lane.get("lane") or "")
        elif item.session is not None:
            lane_label = item.session.lane or ""
        session_id = item.session.session_id if item.session else "-"
        agent = (item.session.agent if item.session else "") or (
            str(item.lane.get("agent_id") or "") if item.lane else ""
        )
        claim_holder = item.claim.agent if item.claim else "-"
        if item.claim is not None and item.claim.expired:
            claim_holder += "(expired)"
        directive = item.directive.state if item.directive else "-"
        findings = ",".join(sorted({f.code for f in item.findings})) or "-"
        lines.append(
            f"{item.issue:>6}  {lane_label[:18]:<18} {session_id[:14]:<14} {agent[:20]:<20} "
            f"{item.status:<8} {claim_holder[:20]:<20} {directive[:10]:<10} {findings}"
        )

    if projection.unreadable:
        lines.append("")
        lines.append("unreadable records:")
        lines.extend(f"  {path}" for path in projection.unreadable)

    findings = [(item.issue, finding) for item in projection.items for finding in item.findings]
    if findings:
        lines.append("")
        lines.append("findings (a second store disagrees, or an artifact is stranded):")
        for issue, finding in findings:
            lines.append(f"  #{issue}  {finding}")

    counts = projection.by_status
    lines.append("")
    lines.append(
        f"fleet-state: live={counts[LIVE]} suspect={counts[SUSPECT]} orphan={counts[ORPHAN]} "
        f"shelved={counts[SHELVED]} absent={counts[ABSENT]} wedged={len(projection.wedged)}"
    )
    if projection.clean:
        lines.append("fleet-state: OK — nothing orphaned, shelved or wedged")
    elif projection.blocking:
        offenders = " ".join(f"#{item.issue}" for item in projection.blocking)
        suffix = f" (+{len(projection.unreadable)} unreadable record(s))" if projection.unreadable else ""
        lines.append(f"fleet-state: NOT-OK — orphaned/shelved/wedged: {offenders}{suffix}")
    else:
        lines.append(
            f"fleet-state: NOT-OK — {len(projection.unreadable)} unreadable record(s); "
            "the projection cannot attest what it cannot read"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# command line
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fleet/state.py",
        description="Project every work item's state by joining the fleet's stores (read-only).",
    )
    parser.add_argument("--root", default=None, help="repository root (default: this checkout)")
    parser.add_argument("--fleet-dir", default=None, help="fleet runtime dir (default: $AO_FLEET_DIR or <root>/.fleet)")
    parser.add_argument("--board-dir", default=None, help="claim ledger dir (default: <root>/.board)")
    parser.add_argument("--ttl-minutes", type=float, default=DEFAULT_TTL_MINUTES, help="session heartbeat TTL")
    parser.add_argument("--at", type=float, default=None, help="evaluate age against this epoch (testing seam)")
    parser.add_argument("--json", action="store_true", help="emit the projection as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve() if args.root else ROOT
    fleet_path = Path(args.fleet_dir) if args.fleet_dir else None
    if fleet_path is None and args.root is None and os.environ.get("AO_FLEET_DIR"):
        fleet_path = Path(os.environ["AO_FLEET_DIR"])
    board_path = Path(args.board_dir) if args.board_dir else None

    resolved_fleet = fleet_path if fleet_path is not None else root / ".fleet"
    resolved_board = board_path if board_path is not None else root / ".board"

    if not resolved_fleet.exists() and not resolved_board.exists():
        print(
            f"fleet-state: CANNOT-ASSESS — neither {resolved_fleet} nor {resolved_board} exists",
            file=sys.stderr,
        )
        return EXIT_CANNOT_ASSESS

    projection = project(
        root=root,
        fleet_dir=resolved_fleet,
        board_dir=resolved_board,
        ttl_minutes=args.ttl_minutes,
        at=args.at,
    )
    if args.json:
        print(json.dumps(projection.to_json(), indent=2, sort_keys=True))
    else:
        print(render_text(projection))
    return EXIT_OK if projection.clean else EXIT_NOT_OK


if __name__ == "__main__":
    sys.exit(main())
