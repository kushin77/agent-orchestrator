"""Leaderboard-style session registry for per-repo agent coordination.

This is the engine-side implementation of the issue #148 contract: each agent
session self-registers, issue state is treated as ground truth, and the registry
computes signals rather than trusting a worker's narration.

Signals are computed from ground truth only — the issue state and the session's
last heartbeat — never from what a session says about itself:

* ``STALE-SESSION`` — a working session whose heartbeat is older than the
  staleness window (the work has silently stopped).
* ``CLAIM-MISMATCH`` — the session claims done but the issue is still open.
* ``WASTED-EFFORT`` — the session is working an issue that is already closed.

---knowledge---
module_id: engine.core.leaderboard
system: engine
app: core
solution_class: pattern
patterns: [domain-model, tenant-scoped]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [SessionRecord, LeaderboardSessionRegistry]
invariants: ""
gotchas: ""
related: []
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional


@dataclass(frozen=True)
class SessionRecord:
    """One self-registering agent session."""

    session_id: str
    agent_id: str
    repo_id: str
    issue_id: str
    worktree: str
    status: str
    self_registered: bool = True
    claimed_done: bool = False
    updated_at: float = 0.0


class LeaderboardSessionRegistry:
    """Registry for per-repo session truth with computed status signals.

    Parameters
    ----------
    stale_after_seconds:
        A working session whose ``updated_at`` is older than this (measured
        against the clock) is reported STALE-SESSION. Defaults to 15 minutes.
    clock:
        Injectable time source (float seconds). Defaults to ``time.time``.
    """

    def __init__(
        self,
        *,
        stale_after_seconds: float = 900.0,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self._sessions: Dict[str, SessionRecord] = {}
        self._issue_state: Dict[str, str] = {}
        self._stale_after = stale_after_seconds
        self._clock = clock if clock is not None else time.time

    def register_session(
        self,
        *,
        session_id: str,
        agent_id: str,
        repo_id: str,
        issue_id: str,
        worktree: str,
        status: str,
        self_registered: bool = True,
        claimed_done: bool = False,
        updated_at: float = 0.0,
    ) -> SessionRecord:
        """Create or refresh a session row.

        Every session self-registers with a unique ID and the repo/worktree pair;
        the registry never permits cross-repo mutation of state.
        """
        record = SessionRecord(
            session_id=session_id,
            agent_id=agent_id,
            repo_id=repo_id,
            issue_id=issue_id,
            worktree=worktree,
            status=status,
            self_registered=self_registered,
            claimed_done=claimed_done,
            updated_at=updated_at,
        )
        self._sessions[session_id] = record
        return record

    def set_issue_state(self, issue_id: str, state: str) -> None:
        self._issue_state[issue_id] = state

    def _issue_state_for(self, issue_id: str) -> str:
        return self._issue_state.get(issue_id, "unknown")

    def summary_for(
        self, session_id: str, *, now: Optional[float] = None
    ) -> Dict[str, object]:
        """The session's computed status, derived from ground truth.

        Signals are computed, never self-narrated: the issue state comes from
        the board and staleness from the heartbeat against the clock.
        """
        record = self._sessions[session_id]
        signals: List[str] = []
        issue_state = self._issue_state_for(record.issue_id)
        at = self._clock() if now is None else now

        # A working session that stopped heartbeating is stale, not healthy.
        if record.status == "working" and (at - record.updated_at) > self._stale_after:
            signals.append("STALE-SESSION")
        if record.claimed_done and issue_state == "open":
            signals.append("CLAIM-MISMATCH")
        if not record.claimed_done and issue_state == "closed" and record.status == "working":
            signals.append("WASTED-EFFORT")

        return {
            "session_id": record.session_id,
            "agent_id": record.agent_id,
            "repo_id": record.repo_id,
            "issue_id": record.issue_id,
            "worktree": record.worktree,
            "status": record.status,
            "issue_state": issue_state,
            "stale": "STALE-SESSION" in signals,
            "signals": signals,
            "self_registered": record.self_registered,
        }

    def advisory_for(self, session_id: str) -> Dict[str, object]:
        summary = self.summary_for(session_id)
        signals = summary["signals"]
        if not signals:
            return {"session_id": session_id, "route": "none", "signal": None}
        signal = signals[0]
        return {
            "session_id": session_id,
            "route": "commander",
            "signal": signal,
            "issue_id": summary["issue_id"],
            "status": summary["status"],
        }
