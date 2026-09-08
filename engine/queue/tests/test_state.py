"""State-machine table tests (issue #22 acceptance: task states).

Covers the lifecycle PENDING -> CLAIMED(lease) -> RUNNING ->
SUCCEEDED/FAILED/DEAD, lease-expiry requeue, orphan reaping and dead-letter
replay arrows, plus the negative cases (illegal transitions must be refused).
"""

from __future__ import annotations

import pytest

from engine.queue.state import (
    DEAD_LETTER_STATES,
    LEASE_STATES,
    OPEN_STATES,
    TERMINAL_STATES,
    TaskState,
    assert_transition,
    transition_allowed,
)

P, C, R, S, F, D = (
    TaskState.PENDING,
    TaskState.CLAIMED,
    TaskState.RUNNING,
    TaskState.SUCCEEDED,
    TaskState.FAILED,
    TaskState.DEAD,
)


def test_states_are_exactly_the_lifecycle_set():
    assert {s.value for s in TaskState} == {
        "PENDING",
        "CLAIMED",
        "RUNNING",
        "SUCCEEDED",
        "FAILED",
        "DEAD",
    }


def test_state_groupings_are_consistent():
    assert LEASE_STATES == {C, R}
    assert OPEN_STATES == {P, C, R}
    assert TERMINAL_STATES == {S, F, D}
    assert DEAD_LETTER_STATES == {F, D}
    # terminal + open partition the full state space
    assert OPEN_STATES | TERMINAL_STATES == set(TaskState)


def test_forward_arrows_are_legal():
    for src, dst in [
        (P, C),  # claim
        (C, R),  # start
        (C, S),  # ack right after claim (degenerate but legal)
        (C, F),  # worker-reported fail
        (R, S),  # ack
        (R, F),  # worker-reported fail
    ]:
        assert transition_allowed(src, dst), f"{src.value} -> {dst.value}"


def test_lease_expiry_requeue_and_dead_letter_arrows_are_legal():
    # reaper: lease expired -> requeue (retry) or dead-letter (DEAD)
    for src in (C, R):
        assert transition_allowed(src, P)
        assert transition_allowed(src, D)


def test_replay_arrows_are_legal():
    for src in (F, D):
        assert transition_allowed(src, P)


def test_terminal_states_are_absorbing():
    for src in (S, F, D):
        for dst in TaskState:
            if dst is src:
                continue
            if dst is P and src in (F, D):
                continue  # replay arrow is legal and tested separately
            assert not transition_allowed(src, dst), (
                f"{src.value} -> {dst.value} must be illegal"
            )


@pytest.mark.parametrize(
    "src,dst",
    [
        (P, S),  # cannot ack an unclaimed task
        (P, F),  # cannot fail an unclaimed task
        (P, D),  # cannot reap an unclaimed task
        (P, R),  # must be claimed before running
        (C, C),  # no self loops
        (R, R),
        (S, P),  # succeeded cannot be requeued except via replay of dead-letter
    ],
)
def test_illegal_transitions_are_refused(src, dst):
    assert not transition_allowed(src, dst)
    with pytest.raises(ValueError):
        assert_transition(src, dst)


def test_dead_letter_buckets_are_distinct_from_success():
    assert S not in DEAD_LETTER_STATES
    assert F in DEAD_LETTER_STATES
    assert D in DEAD_LETTER_STATES
