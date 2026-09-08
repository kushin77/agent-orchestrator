"""Backpressure + bounded per-tenant queues (issue #22 acceptance).

Each tenant may hold at most ``per_tenant_max_pending`` open tasks; beyond
that the authority rejects new work with BackpressureError. Terminating a
task frees its budget slot.
"""

from __future__ import annotations

import pytest

from engine.queue import BackpressureError, JobQueue, TaskState

from conftest import cfg


def test_backpressure_rejects_over_budget(make_queue):
    q = make_queue(config=cfg(per_tenant_max_pending=3))
    for i in range(3):
        q.enqueue({"task_id": f"t{i}", "tenant": "acme"})
    with pytest.raises(BackpressureError):
        q.enqueue({"task_id": "t3", "tenant": "acme"})


def test_backpressure_is_per_tenant(make_queue):
    q = make_queue(config=cfg(per_tenant_max_pending=2))
    q.enqueue({"task_id": "a1", "tenant": "acme"})
    q.enqueue({"task_id": "a2", "tenant": "acme"})
    with pytest.raises(BackpressureError):
        q.enqueue({"task_id": "a3", "tenant": "acme"})
    # a different tenant is unaffected by acme's pressure
    q.enqueue({"task_id": "g1", "tenant": "globex"})
    assert q.get("g1").status is TaskState.PENDING


def test_success_frees_budget_slot(make_queue):
    q = make_queue(config=cfg(per_tenant_max_pending=1))
    q.enqueue({"task_id": "t1", "tenant": "acme"})
    with pytest.raises(BackpressureError):
        q.enqueue({"task_id": "t2", "tenant": "acme"})
    q.claim("w")
    q.ack("t1", "w")
    # slot freed -> admission succeeds again
    q.enqueue({"task_id": "t2", "tenant": "acme"})
    assert q.get("t2").status is TaskState.PENDING


def test_fail_that_deadletters_frees_budget(make_queue):
    q = make_queue(config=cfg(per_tenant_max_pending=1, max_attempts=1))
    q.enqueue({"task_id": "t1", "tenant": "acme"})
    q.claim("w")
    q.fail("t1", "w")  # attempts exhausted -> FAILED (terminal)
    assert q.get("t1").status is TaskState.FAILED
    q.enqueue({"task_id": "t2", "tenant": "acme"})  # budget freed
    assert q.stats()["tasks"] == 2


def test_claimed_and_running_count_toward_budget(make_queue):
    q = make_queue(config=cfg(per_tenant_max_pending=2))
    q.enqueue({"task_id": "t1", "tenant": "acme"})
    q.enqueue({"task_id": "t2", "tenant": "acme"})
    q.claim("w")  # t1 -> CLAIMED (still open, still counts)
    with pytest.raises(BackpressureError):
        q.enqueue({"task_id": "t3", "tenant": "acme"})


def test_depth_reflects_open_tasks(q: JobQueue):
    assert q.depth() == 0
    assert q.depth("acme") == 0
    q.enqueue({"task_id": "a", "tenant": "acme"})
    q.enqueue({"task_id": "g", "tenant": "globex"})
    assert q.depth() == 2
    assert q.depth("acme") == 1
    q.claim("w", tenant="acme")
    q.ack("a", "w")
    assert q.depth("acme") == 0
    assert q.depth() == 1
