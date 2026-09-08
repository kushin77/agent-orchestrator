"""Worker-abstraction tests (issue #22: claim -> run -> ack/fail loop).

The worker never records a false PASS: it acks only on an independently
verified success, and any handler death (exception or ``SystemExit`` — the
Python analogue of a shell ``set -e`` abort) becomes FAILED / a retry, never
SUCCEEDED.
"""

from __future__ import annotations

from engine.queue import (
    JobQueue,
    TaskState,
    Worker,
)

from conftest import cfg

S = TaskState.SUCCEEDED
F = TaskState.FAILED


def test_worker_acks_on_success(q: JobQueue):
    ran = []
    worker = Worker(q, "worker-a", handler=lambda task: ran.append(task.task_id))
    q.enqueue({"task_id": "t1"})
    assert worker.work_once() == "t1"
    assert ran == ["t1"]
    assert q.get("t1").status is S


def test_worker_fails_on_exception(make_queue):
    q = make_queue(config=cfg(max_attempts=1))

    def boom(_task):
        raise RuntimeError("kaboom")

    worker = Worker(q, "worker-a", handler=boom)
    q.enqueue({"task_id": "t1"})
    worker.work_once()
    task = q.get("t1")
    assert task.status is F
    assert "RuntimeError: kaboom" in (task.last_error or "")


def test_worker_never_false_pass_on_set_e_death(make_queue):
    """A handler that dies (set -e style) is FAILED, never SUCCEEDED."""
    q = make_queue(config=cfg(max_attempts=1))

    def die_after_side_effect(task):
        # side effect happened, then the "shell" aborts
        raise SystemExit(1)

    worker = Worker(q, "worker-a", handler=die_after_side_effect)
    q.enqueue({"task_id": "t1"})
    worker.work_once()

    task = q.get("t1")
    assert task.status is F  # recorded FAILED, not SUCCEEDED
    assert "SystemExit" in (task.last_error or "")
    # the audit ledger contains NO transition into SUCCEEDED
    assert not any(
        e.task_id == "t1" and e.to_state is S for e in q.audit_log()
    )


def test_verifier_veto_prevents_false_pass(make_queue):
    """Independent verification: normal return + vetoed result -> FAILED."""
    q = make_queue(config=cfg(max_attempts=1))

    def handler(task):
        return "unverified-ok"

    def verifier(task, outcome):
        return outcome.ok and (task.payload or {}).get("signature") == "verified"

    q.enqueue({"task_id": "t1", "payload": {"signature": "untrusted"}})
    worker = Worker(q, "w", handler=handler, verifier=verifier)
    worker.work_once()
    task = q.get("t1")
    assert task.status is F  # vetoed -> FAILED, never a false PASS
    assert "verifier vetoed" in (task.last_error or "")
    assert not any(e.to_state is S for e in q.audit_log())

    # a genuinely verified result is acked
    q.enqueue({"task_id": "t2", "payload": {"signature": "verified"}})
    worker.work_once()
    assert q.get("t2").status is S


def test_worker_loop_drains_queue(q: JobQueue):
    for i in range(5):
        q.enqueue({"task_id": f"t{i}"})
    worker = Worker(q, "w", handler=lambda task: None)
    assert worker.work_loop() == 5
    assert all(q.get(f"t{i}").status is S for i in range(5))
    # queue drained -> another pass does nothing
    assert worker.work_loop() == 0


def test_worker_loop_limit(q: JobQueue):
    for i in range(5):
        q.enqueue({"task_id": f"t{i}"})
    worker = Worker(q, "w", handler=lambda task: None)
    assert worker.work_loop(limit=2) == 2
    assert q.depth() == 3


def test_worker_retry_after_exception(make_queue):
    """A transient failure with budget left returns to PENDING for retry."""
    q = make_queue(config=cfg(max_attempts=3))

    def flaky(task):
        raise RuntimeError("transient")

    worker = Worker(q, "w", handler=flaky)
    q.enqueue({"task_id": "t1"})
    worker.work_once()
    task = q.get("t1")
    assert task.status is TaskState.PENDING  # requeued, attempt 1 of 3
    assert task.attempts == 1
