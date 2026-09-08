"""Docker runtime tests: pure profile->flag mapping + OFF-by-default refusal.

The Docker runtime is DECLARED and flag-gated OFF (IaC doctrine): the
profile-to-docker-flags mapping is pure and fully tested offline, and every
runtime refuses to run while disabled (fail closed). Nothing here invokes a
docker daemon.
"""

from __future__ import annotations

import pytest

from sandbox.docker import DockerRuntime, docker_run_flags
from sandbox.errors import RuntimeExecutionError, RuntimeNotEnabledError
from sandbox.executor import SandboxExecutor
from sandbox.model import ExecutionRequest


def test_restricted_flags_read_only_drop_all_network_none(catalog):
    flags = docker_run_flags(catalog.profile("restricted"))
    assert "--read-only" in flags
    assert "--cap-drop=ALL" in flags
    assert "--network=none" in flags
    assert "--user=1000:1000" in flags
    assert "--cpu-quota=50000" in flags
    assert "--memory=512m" in flags
    assert "--pids-limit=100" in flags
    assert "--security-opt=no-new-privileges" in flags
    # a read-only profile must never mount the rootfs writable via flags here
    assert "--cap-drop=SYS_ADMIN" not in flags


def test_standard_flags_bridge(catalog):
    flags = docker_run_flags(catalog.profile("standard"))
    assert "--read-only" in flags
    assert "--network=bridge" in flags
    assert "--memory=1024m" in flags
    assert "--pids-limit=200" in flags


def test_privileged_flags_writable_partial_cap_drop_host(catalog):
    flags = docker_run_flags(catalog.profile("privileged"))
    assert "--read-only" not in flags
    assert "--cap-drop=ALL" not in flags
    assert "--cap-drop=SYS_ADMIN" in flags
    assert "--cap-drop=SYS_PTRACE" in flags
    assert "--network=host" in flags
    assert "--memory=2048m" in flags
    assert "--pids-limit=500" in flags
    assert "--user=root" in flags


def test_flag_mapping_is_deterministic(catalog):
    first = docker_run_flags(catalog.profile("standard"))
    second = docker_run_flags(catalog.profile("standard"))
    assert first == second
    assert first[0] == "run"


def test_docker_runtime_ships_disabled_and_refuses():
    runtime = DockerRuntime()
    assert runtime.enabled is False
    with pytest.raises(RuntimeNotEnabledError, match="flag-gated OFF"):
        runtime.run(
            ExecutionRequest(category="exec", tool="exec.sh", operation="shell"),
            None,  # type: ignore[arg-type]  # never reached: refused first
        )


def test_executor_with_off_docker_runtime_fails_closed(catalog):
    executor = SandboxExecutor(runtime=DockerRuntime())
    with pytest.raises(RuntimeNotEnabledError):
        # executor checks runtime.enabled before any run
        executor.execute(
            ExecutionRequest(category="docker", tool="docker.run", operation="run")
        )


def test_enabled_docker_runtime_is_a_declared_seam(catalog):
    # Even when a deployment turns the flag on, this offline repository does
    # not invoke docker: the enabled branch is an explicit declared-seam error,
    # not a silent fake success.
    runtime = DockerRuntime(enabled=True)
    assert runtime.enabled is True
    with pytest.raises(RuntimeExecutionError, match="declared seam"):
        runtime.run(
            ExecutionRequest(category="docker", tool="docker.run", operation="run"),
            catalog.profile("privileged"),
        )
