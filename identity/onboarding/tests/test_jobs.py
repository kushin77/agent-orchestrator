"""Provisioning-job model: status transitions, retry, and step audit
(issue #14, work item 10).

A job records one tenant's attempts (status pending/running/completed/failed,
attempt counter, max attempts, current step, audit log). A failed attempt
leaves no partial tenant (the pipeline is all-or-nothing), and the job record
itself survives the rollback - it is audit history, not a tenant resource.
"""

import pytest

from identity.onboarding import jobs, ready as ready_mod
from identity.onboarding.jobs import (
    JobNotRetryableError,
    retry_job,
    run_job,
    submit_and_run,
)
from identity.onboarding.model import (
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
    JOB_STATUS_PENDING,
)


def test_successful_job_completes_with_audit(stores, make_spec):
    store, rbac_store = stores
    job = submit_and_run(store, rbac_store, make_spec())

    assert job.status == JOB_STATUS_COMPLETED
    assert job.attempt == 1
    assert job.step_status == "ok"
    assert job.completed_at is not None
    assert job.error is None
    assert len(job.audit_log) >= 3  # job open + per-step + job close
    assert store.get_tenant("acme").status == "active"
    report = ready_mod.ready_check(store, rbac_store, "acme")
    assert report.ready


def test_failed_job_records_error_and_survives_rollback(stores, make_spec):
    """A failed attempt leaves NO tenant but a durable failed job record."""
    store, rbac_store = stores
    job = submit_and_run(
        store, rbac_store, make_spec(tenant_type="no-such-pack")
    )

    assert job.status == JOB_STATUS_FAILED
    assert job.error is not None and "no role pack" in job.error
    assert job.step_status == "failed"
    # all-or-nothing: nothing partial remains...
    assert store.get_tenant("acme") is None
    assert rbac_store.org("acme") is None
    # ...yet the audit record persists.
    assert store.get_job(job.id) is job
    assert store.jobs_for_tenant("acme") == [job]


def test_retry_after_fix_increments_attempt_and_completes(stores, make_spec):
    store, rbac_store = stores
    bad = submit_and_run(store, rbac_store, make_spec(tenant_type="no-such-pack"))
    assert bad.status == JOB_STATUS_FAILED
    assert bad.attempt == 1
    assert bad.retryable

    fixed = retry_job(store, rbac_store, bad.id, make_spec())

    assert fixed.id == bad.id
    assert fixed.status == JOB_STATUS_COMPLETED
    assert fixed.attempt == 2
    assert store.get_tenant("acme") is not None
    report = ready_mod.ready_check(store, rbac_store, "acme")
    assert report.ready


def test_job_exhaustion_stops_retries(stores, make_spec):
    store, rbac_store = stores
    spec = make_spec(tenant_type="no-such-pack", max_attempts=1)
    job = submit_and_run(store, rbac_store, spec)

    assert job.status == JOB_STATUS_FAILED
    assert job.attempt == 1
    assert not job.retryable
    with pytest.raises(JobNotRetryableError):
        retry_job(store, rbac_store, job.id, make_spec())


def test_cannot_run_or_retry_a_completed_job(stores, make_spec):
    store, rbac_store = stores
    job = submit_and_run(store, rbac_store, make_spec())
    assert job.status == JOB_STATUS_COMPLETED

    with pytest.raises(JobNotRetryableError):
        run_job(store, rbac_store, job.id, make_spec())
    with pytest.raises(JobNotRetryableError):
        retry_job(store, rbac_store, job.id, make_spec())


def test_new_job_is_pending(stores, make_spec):
    store, _rbac = stores
    job = jobs.new_job(store, "acme")
    assert job.status == JOB_STATUS_PENDING
    assert job.attempt == 0
    assert job.max_attempts == 3
    assert store.get_job(job.id) is job
