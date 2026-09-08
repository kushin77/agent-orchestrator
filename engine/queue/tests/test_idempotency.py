"""At-least-once idempotency tests (issue #22 acceptance).

A redelivered task must not run its side effect twice: enqueueing under an
already-recorded ``task_id`` (strong handle) or ``idempotency_key`` returns
the existing task instead of enqueueing a duplicate — no duplicate side
effects.
"""

from __future__ import annotations

import pytest

from engine.queue import (
    DuplicateTaskError,
    JobQueue,
    TaskState,
)
from engine.queue.dedup import (
    canonical_json,
    fingerprint_payload,
    find_by_idempotency_key,
)


def test_same_idempotency_key_returns_existing_task(q: JobQueue):
    first = q.enqueue({"task_id": "a", "idempotency_key": "ingest-42"})
    second = q.enqueue({"task_id": "b", "idempotency_key": "ingest-42"})
    assert second.task_id == first.task_id == "a"
    assert q.stats()["tasks"] == 1
    assert q.audit_log()[0].reason == "enqueue"  # exactly one enqueue row


def test_same_idempotency_key_across_tenants_is_global(q: JobQueue):
    first = q.enqueue({"task_id": "a", "tenant": "acme", "idempotency_key": "k"})
    second = q.enqueue({"task_id": "b", "tenant": "globex", "idempotency_key": "k"})
    assert second.task_id == first.task_id == "a"
    assert q.stats()["tasks"] == 1


def test_reenqueue_succeeded_task_is_noop(q: JobQueue):
    q.enqueue({"task_id": "t1", "idempotency_key": "k1"})
    q.claim("w")
    q.ack("t1", "w")
    assert q.get("t1").status is TaskState.SUCCEEDED
    # producer retries the delivery after a crash -> same task, no re-run
    again = q.enqueue({"task_id": "t1", "idempotency_key": "k1"})
    assert again.task_id == "t1"
    assert q.get("t1").status is TaskState.SUCCEEDED
    assert q.stats()["tasks"] == 1


def test_reenqueue_deadlettered_task_by_key_is_noop(q: JobQueue):
    q.enqueue({"task_id": "t1", "idempotency_key": "k1"})
    q.claim("w")
    q.fail("t1", "w")  # attempts 1 of 5 -> PENDING (still one task)
    # redelivery under the same key keeps it a single task
    again = q.enqueue({"task_id": "t2", "idempotency_key": "k1"})
    assert again.task_id == "t1"
    assert q.stats()["tasks"] == 1


def test_conflicting_key_on_existing_id_raises(q: JobQueue):
    q.enqueue({"task_id": "t1", "idempotency_key": "k1"})
    with pytest.raises(DuplicateTaskError):
        q.enqueue({"task_id": "t1", "idempotency_key": "other"})


def test_find_by_idempotency_key(q: JobQueue):
    q.enqueue({"task_id": "t1", "idempotency_key": "k1"})
    tasks = {t.task_id: t for t in q.list_tasks()}
    assert find_by_idempotency_key(tasks, "k1").task_id == "t1"
    assert find_by_idempotency_key(tasks, "missing") is None
    assert find_by_idempotency_key(tasks, None) is None


def test_canonical_json_is_key_order_independent():
    a = {"x": 1, "y": [2, 3], "z": {"deep": True}}
    b = {"z": {"deep": True}, "y": [2, 3], "x": 1}
    assert canonical_json(a) == canonical_json(b)


def test_fingerprint_is_stable_and_discriminating():
    p1 = {"job": "build", "ref": "main"}
    p2 = {"job": "build", "ref": "other"}
    assert fingerprint_payload(p1) == fingerprint_payload({"ref": "main", "job": "build"})
    assert fingerprint_payload(p1) != fingerprint_payload(p2)
    assert len(fingerprint_payload(p1)) == 64  # sha256 hex


def test_auto_task_id_generated_when_omitted(q: JobQueue):
    task = q.enqueue({"tenant": "acme", "payload": {"n": 1}})
    assert task.task_id.startswith("t-")
    assert q.get(task.task_id) is not None
