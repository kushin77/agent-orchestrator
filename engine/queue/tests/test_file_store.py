"""FileStore persistence + cross-process single-writer tests.

The file store is the offline analogue of CMR ``fleet/queue.sh``'s mkdir-lock
queue: an flock-guarded JSON file, atomically replaced, so state survives
restarts and two queue instances over the same file cannot split-brain.
"""

from __future__ import annotations

import os

from engine.queue import (
    FileStore,
    JobQueue,
    QueueSnapshot,
    TaskState,
)


def test_file_store_round_trip_persistence(tmp_path):
    path = str(tmp_path / "queue.json")
    q1 = JobQueue(store=FileStore(path))
    q1.enqueue({"task_id": "t1", "tenant": "acme", "payload": {"n": 1}})
    q1.claim("w")
    q1.ack("t1", "w")

    # a brand-new queue over the same file sees the full committed state
    q2 = JobQueue(store=FileStore(path))
    task = q2.get("t1")
    assert task is not None
    assert task.status is TaskState.SUCCEEDED
    assert task.payload == {"n": 1}
    assert q2.stats()["audit_entries"] == 3
    assert len(q2.audit_log()) == 3


def test_file_store_empty_file_loads_as_empty_queue(tmp_path):
    path = str(tmp_path / "queue.json")
    store = FileStore(path)
    snap = store.load()
    assert isinstance(snap, QueueSnapshot)
    assert snap.tasks == {}
    assert snap.audit == []
    assert snap.seq == 0


def test_file_store_atomic_write_leaves_no_temp_files(tmp_path):
    path = str(tmp_path / "queue.json")
    store = FileStore(path)
    q = JobQueue(store=store)
    q.enqueue({"task_id": "t1"})
    q.claim("w")
    q.ack("t1", "w")
    leftovers = [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]
    assert leftovers == []
    assert os.path.exists(path)


def test_two_queues_over_one_file_serialize(tmp_path):
    path = str(tmp_path / "queue.json")
    qa = JobQueue(store=FileStore(path))
    qb = JobQueue(store=FileStore(path))
    for i in range(6):
        qa.enqueue({"task_id": f"t{i}"})

    claimed_a = []
    claimed_b = []
    for i in range(3):
        task = qa.claim(f"wa{i}")
        if task:
            claimed_a.append(task.task_id)
        task = qb.claim(f"wb{i}")
        if task:
            claimed_b.append(task.task_id)

    # between them they claimed six DISTINCT tasks — no overlap
    assert len(claimed_a) + len(claimed_b) == 6
    assert len(set(claimed_a) | set(claimed_b)) == 6
    assert len(set(claimed_a) & set(claimed_b)) == 0


def test_file_store_state_machine_across_restarts(tmp_path, clock):
    """Lease/reap semantics survive a store reopen."""
    path = str(tmp_path / "queue.json")
    q1 = JobQueue(store=FileStore(path), clock=clock)
    q1.enqueue({"task_id": "t1"})
    q1.claim("w", lease_seconds=30)
    clock.advance(31)

    # queue "restarts" (new instance, same file) then reaps the stale claim
    q2 = JobQueue(store=FileStore(path), clock=clock)
    assert q2.reap() == ["t1"]
    assert q2.get("t1").status is TaskState.PENDING
    assert q2.get("t1").attempts == 1
