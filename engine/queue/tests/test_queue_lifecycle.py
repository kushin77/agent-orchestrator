"""Queue lifecycle tests: enqueue -> claim -> run -> ack/fail (issue #22).

Covers the happy path, retry-with-attempts, priority+age claim ordering, and
the no-double-claim / wrong-agent negative cases.
"""

from __future__ import annotations

import pytest

from engine.queue import (
    DuplicateTaskError,
    JobQueue,
    NotClaimedError,
    TaskState,
)
from engine.queue.queue import NoSuchTaskError

from conftest import cfg


def test_full_lifecycle_to_success(q: JobQueue):
    q.enqueue({"task_id": "t1", "tenant": "acme", "payload": {"n": 1}})
    claimed = q.claim("worker-a")
    assert claimed is not None and claimed.task_id == "t1"
    assert q.get("t1").status is TaskState.CLAIMED
    assert q.get("t1").lease_agent == "worker-a"

    running = q.start("t1", "worker-a")
    assert running.status is TaskState.RUNNING

    done = q.ack("t1", "worker-a")
    assert done.status is TaskState.SUCCEEDED
    assert q.get("t1").status is TaskState.SUCCEEDED
    assert q.get("t1").lease_agent is None
    assert q.get("t1").lease_until is None


def test_ack_immediately_after_claim_without_start(q: JobQueue):
    q.enqueue({"task_id": "t1"})
    q.claim("worker-a")
    done = q.ack("t1", "worker-a")
    assert done.status is TaskState.SUCCEEDED


def test_fail_retries_then_dead_letters(make_queue):
    q = make_queue(config=cfg(max_attempts=3))
    q.enqueue({"task_id": "t1"})

    for attempt in (1, 2):
        q.claim("worker-a")
        after = q.fail("t1", "worker-a", reason=f"boom {attempt}")
        # attempt budget remains -> requeued to PENDING
        assert after.status is TaskState.PENDING
        assert after.attempts == attempt
        assert q.get("t1").status is TaskState.PENDING

    q.claim("worker-a")
    final = q.fail("t1", "worker-a", reason="boom 3")
    assert final.status is TaskState.FAILED  # dead-lettered
    assert final.attempts == 3
    assert q.stats()["dead_letter"] == 1


def test_no_double_claim(q: JobQueue):
    q.enqueue({"task_id": "t1"})
    first = q.claim("worker-a")
    assert first is not None and first.task_id == "t1"
    # a second worker cannot claim the same task
    second = q.claim("worker-b")
    assert second is None
    # and the first worker still holds the only claim
    assert q.get("t1").lease_agent == "worker-a"


def test_claim_never_returns_a_claimed_task(q: JobQueue):
    q.enqueue({"task_id": "a"})
    q.enqueue({"task_id": "b"})
    first = q.claim("w1").task_id
    second = q.claim("w2").task_id
    # two workers got two DIFFERENT tasks — no task was claimed twice
    assert {first, second} == {"a", "b"}
    statuses = {t.task_id: t.status for t in q.list_tasks()}
    assert statuses[first] is TaskState.CLAIMED
    assert statuses[second] is TaskState.CLAIMED
    assert q.get(first).lease_agent != q.get(second).lease_agent


def test_ack_by_wrong_agent_is_rejected(q: JobQueue):
    q.enqueue({"task_id": "t1"})
    q.claim("worker-a")
    with pytest.raises(NotClaimedError):
        q.ack("t1", "worker-b")


def test_fail_by_wrong_agent_is_rejected(q: JobQueue):
    q.enqueue({"task_id": "t1"})
    q.claim("worker-a")
    with pytest.raises(NotClaimedError):
        q.fail("t1", "worker-b", reason="nope")


def test_ack_of_unclaimed_task_is_rejected(q: JobQueue):
    q.enqueue({"task_id": "t1"})
    with pytest.raises(NotClaimedError):
        q.ack("t1", "worker-a")


def test_start_by_wrong_agent_is_rejected(q: JobQueue):
    q.enqueue({"task_id": "t1"})
    q.claim("worker-a")
    with pytest.raises(NotClaimedError):
        q.start("t1", "worker-b")


def test_start_is_idempotent_for_holder(q: JobQueue):
    q.enqueue({"task_id": "t1"})
    q.claim("worker-a")
    q.start("t1", "worker-a")
    again = q.start("t1", "worker-a")  # already RUNNING by same agent
    assert again.status is TaskState.RUNNING


def test_ack_of_succeeded_task_is_idempotent(q: JobQueue):
    q.enqueue({"task_id": "t1"})
    q.claim("worker-a")
    q.ack("t1", "worker-a")
    again = q.ack("t1", "worker-a")
    assert again.status is TaskState.SUCCEEDED
    # no extra transition recorded for the duplicate ack
    assert len(q.audit_log()) == 3  # enqueue, claim, ack


def test_claim_priority_order(make_queue):
    q = make_queue()
    q.enqueue({"task_id": "low", "priority": "low"})
    q.enqueue({"task_id": "high", "priority": "high"})
    q.enqueue({"task_id": "normal", "priority": "normal"})

    got = [q.claim("w").task_id for _ in range(3)]
    assert got == ["high", "normal", "low"]


def test_claim_oldest_first_within_priority(make_queue, clock):
    q = make_queue()
    q.enqueue({"task_id": "older"})
    clock.advance(5)
    q.enqueue({"task_id": "newer"})
    got = [q.claim("w").task_id for _ in range(2)]
    assert got == ["older", "newer"]


def test_age_bump_promotes_stale_normal_above_fresh_low(make_queue, clock):
    q = make_queue(config=cfg(age_bump_seconds=1000))
    q.enqueue({"task_id": "old_normal", "priority": "normal"})
    clock.advance(2000)  # old_normal now exceeds the age bump
    q.enqueue({"task_id": "fresh_low", "priority": "low"})
    # old_normal rank = 1 + 1 bump = 2 > fresh_low rank 0
    assert q.claim("w").task_id == "old_normal"


def test_tenant_filtering_isolates_claims(make_queue):
    q = make_queue()
    q.enqueue({"task_id": "for-acme", "tenant": "acme"})
    q.enqueue({"task_id": "for-globex", "tenant": "globex"})
    assert q.claim("w", tenant="acme").task_id == "for-acme"
    # acme tenant drained; globex worker still has its own work
    assert q.claim("w", tenant="globex").task_id == "for-globex"
    assert q.claim("w", tenant="acme") is None


def test_unknown_task_operations_raise(q: JobQueue):
    with pytest.raises(NoSuchTaskError):
        q.ack("missing", "w")
    with pytest.raises(NoSuchTaskError):
        q.start("missing", "w")
    assert q.get("missing") is None


def test_repeated_enqueue_same_id_is_a_noop(q: JobQueue):
    q.enqueue({"task_id": "t1", "payload": {"v": 1}})
    again = q.enqueue({"task_id": "t1", "payload": {"v": 2}})
    assert again.task_id == "t1"
    assert again.payload == {"v": 1}  # original kept; no duplicate side effect
    assert q.stats()["tasks"] == 1


def test_duplicate_id_with_conflicting_key_raises(q: JobQueue):
    q.enqueue({"task_id": "t1", "idempotency_key": "k1"})
    with pytest.raises(DuplicateTaskError):
        q.enqueue({"task_id": "t1", "idempotency_key": "k2"})
