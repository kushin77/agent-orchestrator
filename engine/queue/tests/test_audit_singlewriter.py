"""Audit-ledger + single-writer authority tests (issue #22 acceptance).

Every state transition is appended to an append-only audit ledger with a
strictly monotonic seq. The queue is a single writer: concurrent claimers can
never double-claim, and two queue instances over the same file store cannot
split-brain a task.
"""

from __future__ import annotations

import threading

import pytest

from engine.queue import (
    FileStore,
    JobQueue,
    NotClaimedError,
    TaskState,
)

from conftest import cfg

P, C, R, S = (
    TaskState.PENDING,
    TaskState.CLAIMED,
    TaskState.RUNNING,
    TaskState.SUCCEEDED,
)


def test_full_lifecycle_audit_sequence(q: JobQueue):
    q.enqueue({"task_id": "t1"})
    q.claim("w")
    q.start("t1", "w")
    q.ack("t1", "w")
    transitions = [
        (e.from_state, e.to_state, e.agent) for e in q.audit_log()
    ]
    assert transitions == [
        (None, P, None),  # enqueue
        (P, C, "w"),  # claim
        (C, R, "w"),  # start
        (R, S, "w"),  # ack
    ]


def test_audit_seq_is_monotonic_and_consecutive(q: JobQueue):
    for i in range(3):
        q.enqueue({"task_id": f"t{i}"})
        q.claim(f"w{i}")
        q.ack(f"t{i}", f"w{i}")
    seqs = [e.seq for e in q.audit_log()]
    assert seqs == list(range(1, len(seqs) + 1))
    assert len(seqs) == 9  # 3 x (enqueue, claim, ack)


def test_fail_and_reap_are_recorded(make_queue, clock):
    q = make_queue(config=cfg(max_attempts=1))
    q.enqueue({"task_id": "t1"})
    q.claim("w")
    q.fail("t1", "w", reason="boom")
    q.enqueue({"task_id": "t2"})
    q.claim("w2", lease_seconds=30)
    clock.advance(31)
    q.reap()

    entries = q.audit_log()
    assert any(
        e.task_id == "t1" and e.to_state is TaskState.FAILED for e in entries
    )
    assert any(
        e.task_id == "t2" and e.to_state is TaskState.DEAD for e in entries
    )


def test_single_writer_no_double_claim_under_concurrency(q: JobQueue):
    """Many workers racing for one task: exactly one claim wins."""
    q.enqueue({"task_id": "only"})
    results: list = []
    lock = threading.Lock()

    def _claim(name: str) -> None:
        got = q.claim(name)
        with lock:
            results.append(got.task_id if got else None)

    threads = [threading.Thread(target=_claim, args=(f"w{i}",)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    winners = [r for r in results if r is not None]
    assert len(winners) == 1  # no double-claim
    assert q.get("only").lease_agent == f"w{results.index('only')}"


def test_unique_claims_under_concurrent_pressure(q: JobQueue):
    """M tasks, M workers claiming once each: every task claimed exactly once."""
    n = 12
    for i in range(n):
        q.enqueue({"task_id": f"t{i}"})
    claimed_ids: list = []
    lock = threading.Lock()

    def _claim(name: str) -> None:
        task = q.claim(name)
        if task is not None:
            with lock:
                claimed_ids.append(task.task_id)

    threads = [
        threading.Thread(target=_claim, args=(f"w{i}",)) for i in range(n)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(claimed_ids) == n  # all claimed
    assert len(set(claimed_ids)) == n  # none claimed twice


def test_file_store_no_split_brain_between_instances(tmp_path):
    """Two JobQueues over one FileStore cannot both claim the same task."""
    path = str(tmp_path / "shared-queue.json")
    store_a = FileStore(path)
    store_b = FileStore(path)
    qa = JobQueue(store=store_a)
    qb = JobQueue(store=store_b)

    qa.enqueue({"task_id": "t1"})
    got_a = qa.claim("worker-a")
    assert got_a is not None and got_a.task_id == "t1"

    # worker-b on the other instance cannot steal the claim
    assert qb.claim("worker-b") is None
    with pytest.raises(NotClaimedError):
        qb.ack("t1", "worker-b")

    # worker-a settles on its own instance
    qa.ack("t1", "worker-a")
    assert qb.get("t1").status is TaskState.SUCCEEDED
