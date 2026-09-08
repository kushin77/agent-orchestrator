"""Provisioning-job runner: status, retry and audit (issue #14).

A ``ProvisioningJob`` (see ``model.py``) records one tenant's provisioning
attempts - status (pending/running/completed/failed), an attempt counter, a
max-attempts bound, the current step, and a step-level audit log. The status
vocabulary and field shape mirror the harvested ``TenantProvisioningJob``
model (capital-underwriting ``prisma/schema.prisma``): id, tenant, status,
attempt, maxAttempts, stepName/stepStatus, startedAt/completedAt, error,
auditLog, timestamps.

Because the provision pipeline is all-or-nothing and idempotent, a failed
attempt leaves no partial tenant, and a retry simply converges - re-running a
job can only succeed once the underlying inputs are valid.
"""

from __future__ import annotations

from pathlib import Path

from identity.onboarding import provisioning
from identity.onboarding.model import (
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
    JOB_STATUS_PENDING,
    JOB_STATUS_RUNNING,
    JOB_DEFAULT_MAX_ATTEMPTS,
    AuditEvent,
    ProvisioningJob,
    utcnow_iso,
)


class UnknownJobError(KeyError):
    """No provisioning job with the requested id."""


class JobNotRetryableError(RuntimeError):
    """A job that is not failed, or has exhausted its attempts, cannot retry."""


def new_job(
    store,
    tenant_id: str,
    *,
    max_attempts: int = JOB_DEFAULT_MAX_ATTEMPTS,
) -> ProvisioningJob:
    """Create (and store) a pending provisioning job for a tenant."""
    now = utcnow_iso()
    job = ProvisioningJob(
        id=store.next_id("job"),
        tenant_id=tenant_id,
        status=JOB_STATUS_PENDING,
        max_attempts=max_attempts,
        created_at=now,
        updated_at=now,
    )
    store.put_job(job)
    return job


def run_job(
    store,
    rbac_store,
    job_id: str,
    spec: provisioning.ProvisionSpec,
    *,
    seed_pack: dict | None = None,
    repo_root: Path | None = None,
) -> ProvisioningJob:
    """Execute one attempt of a pending/failed job and record the outcome."""
    job = store.get_job(job_id)
    if job is None:
        raise UnknownJobError(job_id)
    if job.status not in (JOB_STATUS_PENDING, JOB_STATUS_FAILED):
        raise JobNotRetryableError(
            f"job {job_id} is {job.status}; only pending or failed jobs can run"
        )

    now = utcnow_iso()
    job.attempt += 1
    job.status = JOB_STATUS_RUNNING
    job.started_at = now
    job.step_name = None
    job.step_status = None
    job.error = None
    job.audit_log.append(
        AuditEvent(now, "job", "running", f"attempt {job.attempt}/{job.max_attempts}")
    )
    job.updated_at = now
    store.put_job(job)

    try:
        provisioning.provision(
            store,
            rbac_store,
            spec,
            seed_pack=seed_pack,
            repo_root=repo_root,
            job=job,
        )
    except Exception as exc:  # noqa: BLE001 - the job model records any failure
        failed_at = utcnow_iso()
        job.status = JOB_STATUS_FAILED
        job.step_status = "failed"
        job.error = f"{type(exc).__name__}: {exc}"
        job.completed_at = failed_at
        job.updated_at = failed_at
        job.audit_log.append(
            AuditEvent(failed_at, job.step_name or "provision", "failed", str(exc))
        )
        store.put_job(job)
        return job

    completed_at = utcnow_iso()
    job.status = JOB_STATUS_COMPLETED
    job.step_status = "ok"
    job.completed_at = completed_at
    job.updated_at = completed_at
    job.audit_log.append(
        AuditEvent(completed_at, "job", "completed", "provision converged")
    )
    store.put_job(job)
    return job


def retry_job(
    store,
    rbac_store,
    job_id: str,
    spec: provisioning.ProvisionSpec,
    *,
    seed_pack: dict | None = None,
    repo_root: Path | None = None,
) -> ProvisioningJob:
    """Retry a failed job that still has attempts left (else raise)."""
    job = store.get_job(job_id)
    if job is None:
        raise UnknownJobError(job_id)
    if not job.retryable:
        raise JobNotRetryableError(
            f"job {job_id} cannot be retried (status={job.status}, "
            f"attempt={job.attempt}/{job.max_attempts})"
        )
    return run_job(
        store,
        rbac_store,
        job_id,
        spec,
        seed_pack=seed_pack,
        repo_root=repo_root,
    )


def submit_and_run(
    store,
    rbac_store,
    spec: provisioning.ProvisionSpec,
    *,
    seed_pack: dict | None = None,
    repo_root: Path | None = None,
) -> ProvisioningJob:
    """Create a pending job for ``spec`` and run its first attempt."""
    job = new_job(store, spec.slug, max_attempts=spec.max_attempts)
    return run_job(
        store,
        rbac_store,
        job.id,
        spec,
        seed_pack=seed_pack,
        repo_root=repo_root,
    )
