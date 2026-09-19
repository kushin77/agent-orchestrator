"""The lane's session — stamped by ``open``, cleared by close-out, judged by the
audit (issue #917).

Measured on the shared checkout, 2026-09-16: ``.fleet/sessions/`` held **0**
beats while ``.fleet/lanes/`` held 78 records and ``git worktree list`` 110
entries, so ``governance/reconcile`` — whose whole job is to sweep orphaned
sessions — printed ``0 session(s), 0 orphan(s)`` and exited OK. The heartbeat
plane was empty because *nothing wrote to it at the moment a lane came into
being*: the mint (``governance/isolation/cli.py open``) wrote the lane record
and never a beat, and the runtimes that ran the lanes (a Claude session, the
DeepSeek sister, a Copilot agent) each had their own idea of a session that the
sweeper could not see. Session identity was invisible to the tool that sweeps
sessions — the fail-open shape GR-12 names.

This module is the missing write, in the one place every runtime already goes
through: **the lane's session is stamped when the lane is opened**, in the
sweeper's own vocabulary (``governance/reconcile/heartbeat.Session``), keyed by
the lane's session id, and **cleared when the lane is closed out**. Nothing
about it is runtime-shaped — the beat records the pid that owns the lane
(``--pid``, default the parent of the mint), the lane, the branch and the
worktree — so a lane opened by one runtime is visible to, and closable by,
any other.

The audit half: a lane whose session is **gone** is refused by name.
``lane-session-gone:<session>`` means the record was minted with a session
(it carries ``opened_at``) and the session no longer is: the beat is absent or
older than the session TTL (``governance/policy/lease.SESSION_TTL_MINUTES``,
the same value the sweeper judges by) AND the owning process is dead. A stale
beat behind a live pid is not gone — it is a runtime that does not refresh its
beat, which is the sweeper's ``suspect`` case and is reported, never refused.
A legacy record (no ``opened_at``) owes no beat: it predates the plane, and the
reconcile sweep classifies it (``orphan-issue-lane``) rather than this rule
turning 33 pre-existing records red at once.
"""

from __future__ import annotations

import os
from pathlib import Path

from governance.isolation.identity import SessionIdentity
from governance.isolation.violation import Violation

#: The refusal this module owns.
LANE_SESSION_GONE = "lane-session-gone"


def _heartbeat():
    """The sweeper's heartbeat module, or ``None`` where it is not importable.

    Imported lazily and by name: this package is copied on its own into scratch
    roots by other suites' fixtures (``governance/lifecycle/tests/test_lane_records.py``
    copies ``governance/isolation`` alone), and a mint that cannot import the
    sweeper must still mint. Where the module is absent nothing is stamped and
    the session rule measures nothing — ``open`` reports ``session: unstamped``
    rather than pretending, and the lifecycle/reconcile suites (which always
    have both packages) are where the write is proven.
    """
    try:
        from governance.reconcile import heartbeat  # noqa: PLC0415 - optional at import time
    except ImportError:
        return None
    return heartbeat


def default_ttl_minutes() -> float:
    """The session TTL is the SWEEPER's, read from the same declared control, so
    the audit and the sweep can never disagree about when a beat is stale."""
    heartbeat = _heartbeat()
    return float(heartbeat.DEFAULT_TTL_MINUTES) if heartbeat is not None else 15.0


def stamp_for(
    identity: SessionIdentity,
    main: Path | str,
    *,
    pid: int | None = None,
    note: str = "",
    at: float | None = None,
):
    """Write the lane's session beat, keyed by the lane's own session id.

    ``pid`` is the process that OWNS the lane — by default the parent of the
    mint, because the mint itself exits the moment it has printed the identity
    and a beat recording its pid would read as dead one second later. Returns
    the ``heartbeat.Session`` written, or ``None`` where the sweeper's module
    is not importable (see :func:`_heartbeat`).
    """
    heartbeat = _heartbeat()
    if heartbeat is None:
        return None
    return heartbeat.stamp(
        identity.session_id,
        issue=identity.issue,
        agent=identity.agent_id,
        root=main,
        lane=identity.lane,
        worktree=str(identity.worktree),
        branch=identity.branch,
        pid=pid if pid is not None else os.getppid(),
        note=note,
        at=at,
    )


def clear_for(identity: SessionIdentity, main: Path | str) -> bool:
    """Remove the lane's session beat. Returns whether one was there."""
    heartbeat = _heartbeat()
    return heartbeat.clear(identity.session_id, main) if heartbeat is not None else False


def read_for(identity: SessionIdentity, main: Path | str):
    heartbeat = _heartbeat()
    return heartbeat.read(identity.session_id, main) if heartbeat is not None else None


def session_gone(
    identity: SessionIdentity,
    main: Path | str,
    *,
    ttl_minutes: float | None = None,
    at: float | None = None,
    alive: bool | None = None,
) -> list[Violation]:
    """``lane-session-gone`` for a session-minted lane whose session is gone.

    ``at`` and ``alive`` are the seams that make "the beat aged past the TTL and
    the process died" provable without waiting or killing anything — the same
    seams ``heartbeat.judge`` exposes.
    """
    if not identity.opened_at:
        return []
    heartbeat = _heartbeat()
    if heartbeat is None:
        return []
    if ttl_minutes is None:
        ttl_minutes = default_ttl_minutes()
    beat = read_for(identity, main)
    moment = heartbeat.now_epoch() if at is None else at
    if beat is None:
        process_alive = False if alive is None else alive
        if process_alive:
            return []
        return [
            Violation(
                LANE_SESSION_GONE,
                f"lane {identity.session_id} was opened at {identity.opened_at} with a session, and "
                f"no beat exists under {heartbeat.sessions_dir(main)} — the session is gone and the "
                "lane was never closed out; `governance/lifecycle/cli.py close --lane "
                f"{identity.session_id}` finishes it from evidence, or the reconcile sweep names it",
            )
        ]
    age = moment - beat.at
    ttl_seconds = ttl_minutes * 60.0
    process_alive = heartbeat.pid_alive(beat.pid) if alive is None else alive
    if age <= ttl_seconds or process_alive:
        return []
    return [
        Violation(
            LANE_SESSION_GONE,
            f"lane {identity.session_id}'s beat is {int(age)}s old (past the {ttl_minutes:g}m TTL) and "
            f"its process {beat.pid} is gone — the session ended without a close-out; "
            f"`governance/lifecycle/cli.py close --lane {identity.session_id}` finishes it from "
            "evidence, or the reconcile sweep names it",
        )
    ]
