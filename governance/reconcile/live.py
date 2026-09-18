"""A live projection of every beating session against the real disk (#885).

``status`` already reports a session's heartbeat verdict (live/suspect/orphan)
and, with ``--disk``, every worktree/branch no record explains. Neither one
answers the question this module exists for: **does what a session's OWN
heartbeat claims still match what is actually on disk right now?** A session
can be perfectly "live" by heartbeat freshness while its recorded worktree has
already been removed out from under it (a hand `rm -rf`, a half-finished
teardown that crashed after the git step but before it cleared the beat) — the
beat says one thing, the filesystem says another, and nothing before this
module compared them.

``project()`` is a live query, not a cache: it re-reads every session heartbeat
and re-stats every recorded worktree path on each call, so it can never itself
go stale (a materialised snapshot would just move the "is this still true?"
question one level down). Each row is one session, tagged ``matched`` when its
recorded worktree is present on disk (or it never recorded one) and
``drift`` when the beat names a worktree that is not there — the exact
condition ``scripts/check-reconcile.sh``'s live-feed provocation proves.

Exposed through the existing ``status`` verb (``status --live``), not a new
CLI verb — the same reasoning `status --disk` already documents: a new verb is
a surface the control-plane verb registry gates, and that contract belongs to
its own lane.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from governance.modules import schema as frozen_schema  # noqa: E402
from governance.reconcile.heartbeat import Session, list_sessions  # noqa: E402

MATCHED = "matched"
DRIFT = "drift"

SCHEMA_PATH = Path(__file__).resolve().parent / "reconcile.schema.json"


@dataclass(frozen=True)
class LiveRow:
    session_id: str
    issue: int
    worktree: str
    branch: str
    worktree_present: bool
    match: str
    detail: str = ""

    def to_json(self) -> dict:
        return {
            "session_id": self.session_id,
            "issue": self.issue,
            "worktree": self.worktree,
            "branch": self.branch,
            "worktree_present": self.worktree_present,
            "match": self.match,
            "detail": self.detail,
        }


def _row_schema():
    document = frozen_schema.load(SCHEMA_PATH)
    return document["$defs"]["live-row"]


def _project_one(session: Session) -> LiveRow:
    worktree = session.worktree or ""
    present = bool(worktree) and Path(worktree).exists()
    if not worktree or present:
        return LiveRow(
            session_id=session.session_id,
            issue=session.issue,
            worktree=worktree,
            branch=session.branch or "",
            worktree_present=present,
            match=MATCHED,
        )
    return LiveRow(
        session_id=session.session_id,
        issue=session.issue,
        worktree=worktree,
        branch=session.branch or "",
        worktree_present=False,
        match=DRIFT,
        detail=(
            f"heartbeat names worktree {worktree!r} but it is not on disk — "
            "the beat and the real tree disagree"
        ),
    )


def project(root: Path | str) -> list[LiveRow]:
    """One row per session with a heartbeat, compared against the real disk."""
    return [_project_one(session) for session in list_sessions(root)]


def validate(rows: list[LiveRow]) -> None:
    """Every projected row must satisfy its own frozen shape (dogfooding the
    schema this package freezes, not just the ledger's records)."""
    row_schema = _row_schema()
    for row in rows:
        found = frozen_schema.problems(row.to_json(), row_schema)
        if found:
            raise ValueError(
                f"live row for {row.session_id!r} violates reconcile.schema.json"
                f"#/$defs/live-row: {'; '.join(found)}"
            )


def describe(rows: list[LiveRow]) -> str:
    if not rows:
        return "reconcile-live: no sessions"
    drifted = [row for row in rows if row.match == DRIFT]
    lines = [f"reconcile-live: {len(rows)} session(s), {len(drifted)} drifted"]
    for row in drifted:
        lines.append(f"  DRIFT  #{row.issue} {row.session_id} — {row.detail}")
    return "\n".join(lines)
