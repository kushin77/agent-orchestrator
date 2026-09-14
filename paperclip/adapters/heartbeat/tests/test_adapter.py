"""The heartbeat adapter's behavior, anchored to real fleet store shapes."""

from __future__ import annotations

import json

import pytest

from paperclip.adapters.heartbeat import adapter

T1 = "2026-09-14T00:01:00Z"
T2 = "2026-09-14T00:02:00Z"
T0 = "2026-09-14T00:00:05Z"


def _sister_beat(ts: str = T1, *, state: str = "working", **extra: object) -> dict:
    beat = {
        "pid": 4242,
        "state": state,
        "started_at": "2026-09-14T00:00:00Z",
        "commit": "abc1234",
        "ts": ts,
        "issue": 414,
        "agent": "ao414",
    }
    beat.update(extra)
    return beat


def _claim(issue: int = 414, agent: str = "ao414", at: str = T0, **extra: object) -> dict:
    event = {"event": "claim", "issue": issue, "agent": agent, "at": at, "base_commit": "abc1234"}
    event.update(extra)
    return event


def _issue(number: int, *, state: str = "open", blocked_by: list[int] | None = None, closed_at: str = "") -> dict:
    return {
        "number": number,
        "title": f"issue {number}",
        "state": state,
        "milestone": "M27",
        "labels": [],
        "parent": None,
        "blocked_by": blocked_by or [],
        "closed_at": closed_at,
    }


# ── cadence policy: derived from the owners, never re-declared ───────────────


def test_policy_reads_the_owning_constants() -> None:
    assert adapter.cadence_seconds() == 20
    assert adapter.staleness_ceiling_seconds() == 120
    reported = adapter.policy()
    assert reported["cadence_within_ceiling"] is True
    assert reported["cadence_source"] == "fleet/monitor.py:POLL_SECONDS"
    assert reported["ceiling_source"] == "governance/policy/lease.py:RUNG_HEARTBEAT_SECONDS"


def test_policy_fails_closed_without_its_source(tmp_path) -> None:
    with pytest.raises(adapter.HeartbeatRefused) as excinfo:
        adapter.cadence_seconds(tmp_path)
    assert excinfo.value.reason == "source-unreadable"


# ── wake.cause: deterministic attribution from real activity ────────────────


def test_first_beat_is_assigned_progress(fleet_tree) -> None:
    root = fleet_tree(beats={"sister": _sister_beat()}, issues=[_issue(414)], events=[_claim()])
    record = adapter.derive_heartbeat(root, "sister", session_id="s-1")
    assert record["wake"]["cause"] == "assigned"
    assert record["outcome"]["status"] == "progress"
    assert record["tick"] == 0
    assert record["cadence_seconds"] == 20
    assert record["wake"]["delta"]["claimed"] == [414]


def test_no_activity_is_a_scheduled_tick(fleet_tree) -> None:
    root = fleet_tree(beats={"brain": {"pid": 1, "state": "idle", "commit": "abc", "ts": T1}})
    record = adapter.derive_heartbeat(root, "brain", session_id="s-1")
    assert record["wake"]["cause"] == "scheduled"
    assert record["wake"]["delta"]["tick"] == {"from": None, "to": 0}
    assert record["wake"]["delta"]["state"] == {"from": "absent", "to": "idle"}
    assert record["outcome"]["status"] == "progress"


def test_review_requested_when_a_result_lands(fleet_tree) -> None:
    root = fleet_tree(
        beats={"brain": {"pid": 1, "state": "idle", "commit": "abc", "ts": T1}},
        inbox={"brain": [{"id": "m1", "from": "sister", "to": "brain", "type": "result", "ts": T0}]},
    )
    record = adapter.derive_heartbeat(root, "brain", session_id="s-1")
    assert record["wake"]["cause"] == "review-requested"


def test_commented_when_a_non_directive_message_lands(fleet_tree) -> None:
    root = fleet_tree(
        beats={"brain": {"pid": 1, "state": "idle", "commit": "abc", "ts": T1}},
        inbox={"brain": [{"id": "m1", "from": "sister", "to": "brain", "type": "ack", "ts": T0}]},
    )
    record = adapter.derive_heartbeat(root, "brain", session_id="s-1")
    assert record["wake"]["cause"] == "commented"


def test_assigned_when_a_directive_lands(fleet_tree) -> None:
    root = fleet_tree(
        beats={"sister": _sister_beat()},
        inbox={"sister": [{"id": "m1", "from": "brain", "to": "sister", "type": "directive", "ts": T0}]},
    )
    record = adapter.derive_heartbeat(root, "sister", session_id="s-1")
    assert record["wake"]["cause"] == "assigned"


# ── wake.delta: the change since the last beat ──────────────────────────────


def test_delta_carries_the_change_since_the_last_beat(fleet_tree) -> None:
    root = fleet_tree(beats={"sister": _sister_beat()}, issues=[_issue(414)], events=[_claim()])
    first = adapter.derive_heartbeat(root, "sister", session_id="s-1")
    assert first["wake"]["delta"]["claimed"] == [414]

    # The same activity one tick later: the delta carries the tick, not a dump.
    fleet_tree(beats={"sister": _sister_beat(T2)})
    second = adapter.derive_heartbeat(root, "sister", session_id="s-1", history=[first])
    assert second["tick"] == 1
    assert second["wake"]["delta"]["since"] == T1
    assert second["wake"]["delta"]["tick"] == {"from": 0, "to": 1}
    assert "claimed" not in second["wake"]["delta"]
    assert second["wake"]["cause"] == "scheduled"


def test_unblocked_when_the_blocker_closes(fleet_tree) -> None:
    root = fleet_tree(
        beats={"sister": _sister_beat(T1)},
        issues=[
            _issue(400, state="closed", closed_at="2026-09-14T00:01:30Z"),
            _issue(414, blocked_by=[400]),
        ],
        events=[_claim()],
    )
    first = adapter.derive_heartbeat(root, "sister", session_id="s-1")
    assert first["wake"]["delta"]["blocked"] == [414]

    fleet_tree(beats={"sister": _sister_beat(T2)})
    second = adapter.derive_heartbeat(root, "sister", session_id="s-1", history=[first])
    assert second["wake"]["delta"]["unblocked"] == [414]
    assert second["wake"]["cause"] == "unblocked"
    assert second["outcome"]["status"] == "progress"


def test_held_set_release_is_a_released_delta(fleet_tree) -> None:
    root = fleet_tree(
        beats={"sister": _sister_beat(T1)},
        events=[_claim()],
    )
    assert adapter.activity({}, adapter.read_ledger(root), {414}, adapter.parse_iso(T1)).held == frozenset({414})
    events = adapter.read_ledger(root)
    released = events + [{"event": "release", "issue": 414, "agent": "ao414", "at": T2, "base_commit": "", "reason": ""}]
    assert adapter.activity({}, released, {414}, adapter.parse_iso(T2)).held == frozenset()


# ── fail-closed: never fabricate a cause it cannot source ───────────────────


def test_no_beat_is_a_blocked_outcome_with_a_named_owner(fleet_tree) -> None:
    root = fleet_tree()
    record = adapter.derive_heartbeat(root, "sister", session_id="s-1", now=T1)
    assert record["wake"]["cause"] == "scheduled"
    assert record["outcome"]["status"] == "blocked"
    assert record["outcome"]["owner"] == "brain"
    assert record["wake"]["delta"]["state"] == {"from": "absent", "to": "no-heartbeat"}


def test_unknown_state_fails_closed(fleet_tree) -> None:
    root = fleet_tree(beats={"sister": _sister_beat(state="quantum")})
    with pytest.raises(adapter.HeartbeatRefused) as excinfo:
        adapter.derive_heartbeat(root, "sister", session_id="s-1")
    assert excinfo.value.reason == "unknown-state"
    assert "quantum" in str(excinfo.value)


def test_unknown_rung_fails_closed(fleet_tree) -> None:
    root = fleet_tree()
    with pytest.raises(adapter.HeartbeatRefused) as excinfo:
        adapter.derive_heartbeat(root, "ghost", session_id="s-1", now=T1)
    assert excinfo.value.reason == "unknown-rung"


def test_unreadable_ledger_fails_closed(fleet_tree) -> None:
    root = fleet_tree(beats={"sister": _sister_beat()})
    (root / ".board").mkdir(parents=True, exist_ok=True)
    (root / ".board" / "claims.jsonl").write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(adapter.HeartbeatRefused) as excinfo:
        adapter.derive_heartbeat(root, "sister", session_id="s-1")
    assert excinfo.value.reason == "ledger-unreadable"


# ── the validator refuses by name ───────────────────────────────────────────


def _valid_record(fleet_tree) -> dict:
    root = fleet_tree(beats={"sister": _sister_beat()}, issues=[_issue(414)], events=[_claim()])
    return adapter.derive_heartbeat(root, "sister", session_id="s-1")


def test_derived_beat_conforms_to_the_frozen_schema(fleet_tree) -> None:
    assert adapter.validate_heartbeat(_valid_record(fleet_tree)) == []


def test_validator_refuses_a_beat_with_no_delta(fleet_tree) -> None:
    record = _valid_record(fleet_tree)
    record["wake"]["delta"] = {}
    findings = adapter.validate_heartbeat(record)
    assert any("wake.delta" in f and "no delta" in f for f in findings), findings


def test_validator_refuses_an_unknown_wake_cause(fleet_tree) -> None:
    record = _valid_record(fleet_tree)
    record["wake"]["cause"] = "teleport"
    findings = adapter.validate_heartbeat(record)
    assert any("wake.cause" in f and "teleport" in f for f in findings), findings


def test_validator_refuses_blocked_without_an_owner(fleet_tree) -> None:
    record = _valid_record(fleet_tree)
    record["outcome"] = {"status": "blocked", "detail": "stuck", "owner": ""}
    findings = adapter.validate_heartbeat(record)
    assert any("outcome.owner" in f for f in findings), findings


def test_validator_refuses_an_outcome_that_is_neither(fleet_tree) -> None:
    record = _valid_record(fleet_tree)
    record["outcome"] = {"status": "maybe", "detail": "?", "owner": "someone"}
    findings = adapter.validate_heartbeat(record)
    assert any("outcome.status" in f for f in findings), findings


# ── determinism + the emit count ────────────────────────────────────────────


def test_derive_is_deterministic(fleet_tree) -> None:
    root = fleet_tree(beats={"sister": _sister_beat()}, issues=[_issue(414)], events=[_claim()])
    first = adapter.derive_heartbeat(root, "sister", session_id="s-1")
    second = adapter.derive_heartbeat(root, "sister", session_id="s-1")
    assert json.dumps(first) == json.dumps(second)


def test_tick_is_the_actual_emit_count(fleet_tree) -> None:
    root = fleet_tree(beats={"brain": {"pid": 1, "state": "idle", "commit": "abc", "ts": T1}})
    history = [
        {
            "tick": index,
            "ts": f"2026-09-14T00:00:0{index}Z",
            "wake": {"delta": {"state": {"to": "idle"}}},
            "outcome": {"status": "progress"},
        }
        for index in range(3)
    ]
    record = adapter.derive_heartbeat(root, "brain", session_id="s-1", history=history)
    assert record["tick"] == 3
    assert record["wake"]["delta"]["tick"] == {"from": 2, "to": 3}
