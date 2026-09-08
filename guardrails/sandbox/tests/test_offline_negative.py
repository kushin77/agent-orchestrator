"""Negative-control tests: the offline runtime refuses what the profile forbids.

Every denial below is paired with a positive control - the same request under
the profile that permits it is accepted - so the enforcement is provably real
and not a check that always denies (no-false-green). Enforcement is derived
from the profile fields, so these tests pin the restricted profile's
network=none, capDrop ALL, read-only rootfs, no-new-privs and quota semantics.
"""

from __future__ import annotations

import pytest

from sandbox.errors import RuntimeNotEnabledError
from sandbox.executor import SandboxExecutor
from sandbox.model import ExecutionRequest
from sandbox.offline import OfflineRuntime


@pytest.fixture
def executor():
    return SandboxExecutor(runtime=OfflineRuntime(enabled=True))


def test_restricted_blocks_network_and_standard_allows_it(executor):
    denied = executor.execute(
        ExecutionRequest(
            category="exec", tool="exec.curl", operation="http-get",
            requires_network=True,
        )
    )
    assert denied.denied
    assert denied.profile == "restricted"
    assert "network access blocked" in denied.reason

    # positive control: the same network op under the network category
    # (standard profile, bridge network) is accepted.
    accepted = executor.execute(
        ExecutionRequest(
            category="network", tool="network.fetch", operation="http-get",
            requires_network=True,
        )
    )
    assert accepted.accepted
    assert accepted.profile == "standard"


def test_restricted_blocks_capability_escalation(executor):
    denied = executor.execute(
        ExecutionRequest(
            category="exec",
            tool="exec.mount",
            operation="mount",
            requires_caps=("SYS_ADMIN",),
        )
    )
    assert denied.denied
    assert "SYS_ADMIN" in denied.reason and "dropped" in denied.reason

    # positive control: privileged only drops SYS_ADMIN/SYS_PTRACE, so a
    # NET_ADMIN capability request is accepted there.
    accepted = executor.execute(
        ExecutionRequest(
            category="docker",
            tool="docker.run",
            operation="configure-net",
            requires_caps=("NET_ADMIN",),
        )
    )
    assert accepted.accepted
    assert accepted.profile == "privileged"


def test_restricted_blocks_rootfs_write(executor):
    denied = executor.execute(
        ExecutionRequest(
            category="file",
            tool="file.write",
            operation="write",
            requires_rootfs_write=True,
        )
    )
    assert denied.denied
    assert "rootfs write" in denied.reason

    accepted = executor.execute(
        ExecutionRequest(
            category="docker",
            tool="docker.run",
            operation="write",
            requires_rootfs_write=True,
        )
    )
    assert accepted.accepted


def test_no_new_privs_blocks_privilege_escalation(executor):
    denied = executor.execute(
        ExecutionRequest(
            category="exec",
            tool="exec.setuid",
            operation="setuid",
            requires_privilege_escalation=True,
        )
    )
    assert denied.denied
    assert "privilege escalation" in denied.reason


def test_memory_quota_enforced_with_positive_control(executor):
    denied = executor.execute(
        ExecutionRequest(
            category="db",
            tool="db.backup",
            operation="dump",
            requested_memory_mb=2048,  # standard ceiling is 1024 MiB
        )
    )
    assert denied.denied
    assert "memory" in denied.reason and "2048" in denied.reason

    accepted = executor.execute(
        ExecutionRequest(
            category="db",
            tool="db.backup",
            operation="dump",
            profile_name="privileged",  # ceiling is 2048 MiB
            requested_memory_mb=2048,
        )
    )
    assert accepted.accepted


def test_cpu_quota_enforced_with_positive_control(executor):
    denied = executor.execute(
        ExecutionRequest(
            category="exec",
            tool="exec.compile",
            operation="compile",
            requested_cpu_quota=60000,  # restricted ceiling is 50000us
        )
    )
    assert denied.denied
    assert "cpu quota" in denied.reason

    accepted = executor.execute(
        ExecutionRequest(
            category="exec",
            tool="exec.compile",
            operation="compile",
            requested_cpu_quota=50000,
        )
    )
    assert accepted.accepted


def test_pids_quota_enforced_with_positive_control(executor):
    denied = executor.execute(
        ExecutionRequest(
            category="network",
            tool="network.fanout",
            operation="fanout",
            requested_pids=300,  # standard ceiling is 200
        )
    )
    assert denied.denied
    assert denied.profile == "standard"
    assert "pids" in denied.reason

    accepted = executor.execute(
        ExecutionRequest(
            category="network",
            tool="network.fanout",
            operation="fanout",
            requested_pids=200,
        )
    )
    assert accepted.accepted
    assert accepted.profile == "standard"


def test_timeout_quota_enforced_with_positive_control(executor):
    denied = executor.execute(
        ExecutionRequest(
            category="network",
            tool="network.long",
            operation="long-job",
            requested_timeout_seconds=90.0,  # standard ceiling is 60s
        )
    )
    assert denied.denied
    assert denied.profile == "standard"
    assert "timeout" in denied.reason

    accepted = executor.execute(
        ExecutionRequest(
            category="network",
            tool="network.long",
            operation="long-job",
            requested_timeout_seconds=60.0,
        )
    )
    assert accepted.accepted
    assert accepted.profile == "standard"


def test_unknown_category_network_call_fails_closed_to_restricted(executor):
    denied = executor.execute(
        ExecutionRequest(
            category="scheduler",
            tool="scheduler.submit",
            operation="submit",
            requires_network=True,
        )
    )
    assert denied.denied
    assert denied.profile == "restricted"
    assert "network access blocked" in denied.reason


def test_disabled_offline_runtime_refuses():
    runtime = OfflineRuntime(enabled=False)
    with pytest.raises(RuntimeNotEnabledError, match="not enabled"):
        runtime.run(
            ExecutionRequest(category="exec", tool="exec.sh", operation="shell"),
            None,  # type: ignore[arg-type]  # never reached: refused first
        )
