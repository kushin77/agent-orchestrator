"""Dead-letter + replay tests (issue #22 acceptance).

FAILED (worker-reported failure exhausted) and DEAD (reaped exhaustion) are
the dead-letter buckets. ``replay`` returns them to PENDING with the attempt
budget reset so they become claimable again — the offline replay API.
"""

from __future__ import annotations

import pytest

from engine.queue import (
    JobQueue,
    NotReplayableError,
    TaskState,
)
from engine.queue.queue import NoSuchTaskError

from conftest import cfg

P, F, D = TaskState.PENDING, TaskState.FAILED, TaskState.DEAD


def test_worker_reported_exhaustion_lands_in_failed(make_queue):
    q = make_queue(config=cfg(max_attempts=3))
    q.enqueue({"task_id": "t1"})
    for _ in range(3):
        q.claim("w")
        q.fail("t1", "w", reason="boom")
    assert q.get("t1").status is F
    assert q.get("t1").attempts == 3
    assert q.stats()["dead_letter"] == 1


def test_dead_lettered_task_is_not_claimable(make_queue):
    q = make_queue(config=cfg(max_attempts=2))
    q.enqueue({"task_id": "t1"})
    for _ in range(2):
        q.claim("w")
        q.fail("t1", "w", reason="boom")
    assert q.get("t1").status is F
    assert q.claim("w") is None  # no silent re-delivery of a dead letter


def test_replay_single_task_returns_it_to_pending(make_queue):
    q = make_queue(config=cfg(max_attempts=1))
    q.enqueue({"task_id": "t1"})
    q.claim("w")
    q.fail("t1", "w", reason="boom")
    assert q.get("t1").status is F

    replayed = q.replay("t1")
    assert replayed == ["t1"]
    task = q.get("t1")
    assert task.status is P
    assert task.attempts == 0
    # claimable again after replay
    q.claim("w2")
    q.ack("t1", "w2")
    assert q.get("t1").status is TaskState.SUCCEEDED


def test_replay_all_dead_letters(make_queue):
    q = make_queue(config=cfg(max_attempts=1))
    for tid in ("a", "b", "c"):
        q.enqueue({"task_id": tid})
        q.claim("w")
        q.fail(tid, "w", reason="boom")
    assert q.stats()["dead_letter"] == 3
    assert sorted(q.replay()) == ["a", "b", "c"]
    assert q.stats()["dead_letter"] == 0
    assert all(q.get(t).status is P for t in ("a", "b", "c"))


def test_replay_dead_from_reaper(make_queue, clock):
    q = make_queue(config=cfg(max_attempts=1))
    q.enqueue({"task_id": "t1"})
    q.claim("w", lease_seconds=30)
    clock.advance(31)
    q.reap()  # attempts exhausted -> DEAD
    assert q.get("t1").status is D

    q.replay("t1")
    assert q.get("t1").status is P
    assert q.get("t1").attempts == 0


def test_replay_negative_cases(q: JobQueue):
    q.enqueue({"task_id": "pending"})
    with pytest.raises(NotReplayableError):
        q.replay("pending")  # not dead-lettered

    q.claim("w")
    q.ack("pending", "w")
    with pytest.raises(NotReplayableError):
        q.replay("pending")  # SUCCEEDED cannot be replayed

    with pytest.raises(NoSuchTaskError):
        q.replay("missing")


def test_replay_is_recorded_in_audit(make_queue):
    q = make_queue(config=cfg(max_attempts=1))
    q.enqueue({"task_id": "t1"})
    q.claim("w")
    q.fail("t1", "w", reason="boom")
    q.replay("t1")
    transitions = [
        (e.to_state, e.reason) for e in q.audit_log() if e.task_id == "t1"
    ]
    assert (TaskState.PENDING, "replay") in transitions


def test_fail_is_idempotent_on_dead_letter(make_queue):
    q = make_queue(config=cfg(max_attempts=1))
    q.enqueue({"task_id": "t1"})
    q.claim("w")
    q.fail("t1", "w", reason="boom")
    assert q.get("t1").status is F
    again = q.fail("t1", "w", reason="again")
    assert again.status is F
    assert q.get("t1").attempts == 1
