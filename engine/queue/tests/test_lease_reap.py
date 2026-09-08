"""Lease, lease-expiry requeue and the orphan reaper (issue #22).

A claimed task carries a lease; the holder heartbeats with ``renew``. When the
lease expires with no heartbeat the worker is presumed dead — the orphan
reaper requeues the task (attempts + 1) or dead-letters it to DEAD when the
attempt budget is spent. A task whose worker died is never recorded SUCCEEDED.
"""

from __future__ import annotations

import pytest

from engine.queue import (
    JobQueue,
    LeaseError,
    NotClaimedError,
    TaskState,
)

from conftest import cfg


def test_lease_is_set_on_claim(q: JobQueue):
    q.enqueue({"task_id": "t1"})
    claimed = q.claim("worker-a", lease_seconds=30)
    assert claimed.lease_until == claimed.updated_at + 30
    assert q.get("t1").lease_agent == "worker-a"


def test_ack_after_lease_expiry_is_rejected(q: JobQueue, clock):
    q.enqueue({"task_id": "t1"})
    q.claim("worker-a", lease_seconds=30)
    clock.advance(31)
    with pytest.raises(LeaseError):
        q.ack("t1", "worker-a")


def test_start_after_lease_expiry_is_rejected(q: JobQueue, clock):
    q.enqueue({"task_id": "t1"})
    q.claim("worker-a", lease_seconds=30)
    clock.advance(31)
    with pytest.raises(LeaseError):
        q.start("t1", "worker-a")


def test_renew_keeps_claim_alive(make_queue, clock):
    q = make_queue()
    q.enqueue({"task_id": "t1"})
    q.claim("worker-a", lease_seconds=30)
    clock.advance(20)
    q.renew("t1", "worker-a", lease_seconds=30)  # heartbeat
    clock.advance(25)  # past the ORIGINAL expiry, inside the renewed one
    done = q.ack("t1", "worker-a")  # still the holder -> works
    assert done.status is TaskState.SUCCEEDED


def test_renew_by_non_holder_is_rejected(q: JobQueue):
    q.enqueue({"task_id": "t1"})
    q.claim("worker-a")
    with pytest.raises(NotClaimedError):
        q.renew("t1", "worker-b")


def test_reap_requeues_expired_claim(make_queue, clock):
    q = make_queue()
    q.enqueue({"task_id": "t1"})
    q.claim("worker-a", lease_seconds=30)
    q.start("t1", "worker-a")  # worker begins; then dies without ack/fail

    clock.advance(31)  # lease lapses -> orphan
    reaped = q.reap()
    assert reaped == ["t1"]
    task = q.get("t1")
    assert task.status is TaskState.PENDING  # requeued, NOT succeeded
    assert task.attempts == 1
    assert task.lease_agent is None
    # idempotent: nothing left to reap
    assert q.reap() == []


def test_reaped_task_is_claimable_again(make_queue, clock):
    q = make_queue()
    q.enqueue({"task_id": "t1"})
    q.claim("worker-a", lease_seconds=30)
    clock.advance(31)
    q.reap()
    re_claimed = q.claim("worker-b")
    assert re_claimed is not None and re_claimed.task_id == "t1"
    assert re_claimed.attempts == 1


def test_reap_to_dead_when_attempts_exhausted(make_queue, clock):
    q = make_queue(config=cfg(max_attempts=1))
    q.enqueue({"task_id": "t1"})
    q.claim("worker-a", lease_seconds=30)
    clock.advance(31)
    reaped = q.reap()
    assert reaped == ["t1"]
    assert q.get("t1").status is TaskState.DEAD  # dead-lettered
    assert q.stats()["dead_letter"] == 1
    # DEAD tasks are not claimable
    assert q.claim("worker-b") is None


def test_reap_ignores_valid_leases(q: JobQueue, clock):
    q.enqueue({"task_id": "t1"})
    q.claim("worker-a", lease_seconds=300)
    clock.advance(30)  # still well within lease
    assert q.reap() == []
    assert q.get("t1").status is TaskState.CLAIMED


def test_reap_ignores_terminal_and_pending(q: JobQueue, clock):
    q.enqueue({"task_id": "pending"})
    q.enqueue({"task_id": "done"})
    q.claim("worker-a", lease_seconds=30)
    q.ack("done", "worker-a")
    clock.advance(60)
    assert q.reap() == []
    assert q.get("pending").status is TaskState.PENDING
    assert q.get("done").status is TaskState.SUCCEEDED


def test_stale_agent_cannot_ack_after_reap(make_queue, clock):
    """No split-brain: a dead worker's late ack must be refused post-reap."""
    q = make_queue()
    q.enqueue({"task_id": "t1"})
    q.claim("worker-a", lease_seconds=30)
    q.start("t1", "worker-a")
    clock.advance(31)
    q.reap()  # worker-a looked dead -> requeued
    q.claim("worker-b")  # redelivered to worker-b
    # worker-a "wakes up" and tries to ack its old claim -> refused
    with pytest.raises(NotClaimedError):
        q.ack("t1", "worker-a")


def test_lease_expiry_between_claim_and_start_reaps(make_queue, clock):
    q = make_queue()
    q.enqueue({"task_id": "t1"})
    q.claim("worker-a", lease_seconds=30)
    clock.advance(31)
    assert q.reap() == ["t1"]
    assert q.get("t1").status is TaskState.PENDING
