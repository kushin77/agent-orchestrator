"""Falsifiability tests — the gate never lies (issue #22, leaderboard doctrine).

A task whose worker died on ``set -e`` (here: ``SystemExit``) after a side
effect must be recorded FAILED, never SUCCEEDED; redelivery under an
idempotency key must not duplicate the side effect; and every "done" verdict
is independently earned — nothing is a false PASS by construction.
"""

from __future__ import annotations

from engine.queue import (
    TaskState,
    Worker,
)

from conftest import cfg

S = TaskState.SUCCEEDED
F = TaskState.FAILED


def test_dead_after_side_effect_is_failed_not_succeeded(make_queue):
    """Crash AFTER the side effect, BEFORE the ack -> FAILED, no false PASS."""
    q = make_queue(config=cfg(max_attempts=1))
    applied = []

    def handler(task):
        applied.append(task.task_id)  # the side effect
        raise SystemExit(1)  # shell `set -e` death right after it

    worker = Worker(q, "w", handler=handler)
    q.enqueue({"task_id": "t1"})
    worker.work_once()

    assert q.get("t1").status is F  # recorded FAILED
    assert not any(e.to_state is S for e in q.audit_log())  # never SUCCEEDED
    # the side effect ran, but the queue honestly reports the task failed
    assert applied == ["t1"]


def test_idempotency_key_prevents_duplicate_side_effect(make_queue):
    """At-least-once delivery + keyed handler => exactly-once side effect.

    Redelivery of the same task idempotency-key must not re-apply the effect.
    """
    q = make_queue(config=cfg(max_attempts=5))
    effects: dict = {}

    def keyed_handler(task):
        if task.idempotency_key in effects:
            return  # already applied on a previous attempt — skip
        effects[task.idempotency_key] = "applied"
        raise SystemExit(1)  # crash after the effect, before the ack

    worker = Worker(q, "w", handler=keyed_handler)
    q.enqueue({"task_id": "t1", "idempotency_key": "job-1"})

    worker.work_once()  # attempt 1: applies effect, then dies -> FAILED(PENDING retry)
    assert q.get("t1").status is TaskState.PENDING
    assert q.get("t1").attempts == 1
    assert effects == {"job-1": "applied"}

    worker.work_once()  # attempt 2: key present -> no new effect, clean ack
    task = q.get("t1")
    assert task.status is S
    assert task.attempts == 1  # attempts counts failed deliveries only
    # side effect happened EXACTLY once despite two deliveries
    assert effects == {"job-1": "applied"}
    assert len(effects) == 1


def test_worker_dies_without_ack_is_reaped_not_succeeded(make_queue, clock):
    """Whole-worker death (no ack, no fail) -> reaper requeues, never SUCCEEDED."""
    q = make_queue(config=cfg(max_attempts=1))
    q.enqueue({"task_id": "t1"})
    q.claim("worker", lease_seconds=30)
    q.start("t1", "worker")  # worker begins... and the process is killed
    clock.advance(31)
    reaped = q.reap()
    assert reaped == ["t1"]
    assert q.get("t1").status is TaskState.DEAD  # dead-lettered honestly
    assert not any(e.to_state is S for e in q.audit_log())


def test_stats_never_report_a_dead_task_as_succeeded(make_queue):
    q = make_queue(config=cfg(max_attempts=1))
    q.enqueue({"task_id": "t1"})
    q.claim("w")
    q.fail("t1", "w", reason="boom")
    stats = q.stats()
    assert stats["by_status"].get("SUCCEEDED", 0) == 0
    assert stats["by_status"]["FAILED"] == 1
    assert stats["dead_letter"] == 1


def test_claim_after_exhaustion_returns_nothing(make_queue):
    """Exhausted/dead-lettered tasks are not silently re-delivered."""
    q = make_queue(config=cfg(max_attempts=1))
    q.enqueue({"task_id": "t1"})
    q.claim("w")
    q.fail("t1", "w", reason="boom")
    assert q.claim("w2") is None
