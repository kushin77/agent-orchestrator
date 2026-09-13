"""Leaderboard session-registry signals (issue #148).

Signals are computed from ground truth — the issue state and the session
heartbeat — so every scenario here injects a fixed clock rather than depending
on wall-clock time.
"""

from __future__ import annotations

from core.leaderboard import LeaderboardSessionRegistry

NOW = 1_000_000.0


def _registry(stale_after_seconds: float = 60.0) -> LeaderboardSessionRegistry:
    """A registry with a frozen clock, so staleness is deterministic."""
    return LeaderboardSessionRegistry(
        stale_after_seconds=stale_after_seconds, clock=lambda: NOW
    )


def test_session_self_registers_and_tracks_issue_ground_truth():
    registry = _registry()

    session = registry.register_session(
        session_id="s-1",
        agent_id="worker-a",
        repo_id="agent-orchestrator",
        issue_id="148",
        worktree="/tmp/agent-orchestrator-148",
        status="working",
        updated_at=NOW,  # fresh heartbeat
    )

    registry.set_issue_state("148", "open")
    summary = registry.summary_for("s-1")

    assert session.session_id == "s-1"
    assert session.self_registered is True
    assert summary["status"] == "working"
    assert summary["issue_state"] == "open"
    assert summary["stale"] is False
    assert summary["signals"] == []


def test_stale_session_is_detected_from_the_heartbeat():
    """A working session that stopped heartbeating is stale, not healthy."""
    registry = _registry(stale_after_seconds=60.0)
    registry.register_session(
        session_id="s-stale",
        agent_id="worker-e",
        repo_id="agent-orchestrator",
        issue_id="148",
        worktree="/tmp/agent-orchestrator-148",
        status="working",
        updated_at=NOW - 61.0,  # last heartbeat past the window
    )
    registry.set_issue_state("148", "open")

    summary = registry.summary_for("s-stale")
    assert "STALE-SESSION" in summary["signals"]
    assert summary["stale"] is True
    # The session still claims to be working — the registry disagrees.
    assert summary["status"] == "working"


def test_fresh_heartbeat_is_not_stale():
    """The negative control: a session inside the window is not flagged."""
    registry = _registry(stale_after_seconds=60.0)
    registry.register_session(
        session_id="s-fresh",
        agent_id="worker-f",
        repo_id="agent-orchestrator",
        issue_id="148",
        worktree="/tmp/agent-orchestrator-148",
        status="working",
        updated_at=NOW - 59.0,  # still inside the window
    )
    registry.set_issue_state("148", "open")

    summary = registry.summary_for("s-fresh")
    assert summary["signals"] == []
    assert summary["stale"] is False


def test_claim_mismatch_and_wasted_effort_are_detected():
    registry = _registry()
    registry.register_session(
        session_id="s-claim",
        agent_id="worker-b",
        repo_id="agent-orchestrator",
        issue_id="148",
        worktree="/tmp/agent-orchestrator-148",
        status="done",
        claimed_done=True,
    )
    registry.set_issue_state("148", "open")
    assert registry.summary_for("s-claim")["signals"] == ["CLAIM-MISMATCH"]

    registry.register_session(
        session_id="s-waste",
        agent_id="worker-c",
        repo_id="agent-orchestrator",
        issue_id="149",
        worktree="/tmp/agent-orchestrator-149",
        status="working",
        updated_at=NOW,  # fresh heartbeat, so staleness is not a factor
    )
    registry.set_issue_state("149", "closed")
    assert registry.summary_for("s-waste")["signals"] == ["WASTED-EFFORT"]


def test_advisor_channel_escalates_only_when_the_truth_is_bad():
    registry = _registry()
    registry.register_session(
        session_id="s-advise",
        agent_id="worker-d",
        repo_id="agent-orchestrator",
        issue_id="148",
        worktree="/tmp/agent-orchestrator-148",
        status="done",
        claimed_done=True,
    )
    registry.set_issue_state("148", "open")

    advisory = registry.advisory_for("s-advise")
    assert advisory["route"] == "commander"
    assert advisory["signal"] == "CLAIM-MISMATCH"

    registry.set_issue_state("148", "closed")
    assert registry.advisory_for("s-advise")["route"] == "none"
