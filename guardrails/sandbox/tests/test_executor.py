"""Sandbox executor tests: fail-closed resolution, disabled refusals, audit."""

from __future__ import annotations

import pytest

from sandbox.errors import SandboxConfigError, SandboxDisabledError
from sandbox.executor import SandboxExecutor
from sandbox.model import ExecutionRequest, ExecutionResult
from sandbox.offline import OfflineRuntime


def test_executor_requires_an_injected_runtime():
    with pytest.raises(SandboxConfigError, match="runtime"):
        SandboxExecutor(runtime=None)


def test_executor_disabled_refuses_everything(executor):
    disabled = SandboxExecutor(
        runtime=OfflineRuntime(enabled=True), enabled=False
    )
    with pytest.raises(SandboxDisabledError, match="not enabled"):
        disabled.execute(
            ExecutionRequest(category="exec", tool="exec.sh", operation="shell")
        )


def test_executor_refuses_when_runtime_flag_is_off():
    # A runtime that ships flag-gated OFF must make the executor fail closed -
    # a disabled sandbox never degrades into an unsandboxed run.
    runtime = OfflineRuntime(enabled=False)
    executor = SandboxExecutor(runtime=runtime)
    with pytest.raises(SandboxDisabledError, match="flag-gated OFF"):
        executor.execute(
            ExecutionRequest(category="exec", tool="exec.sh", operation="shell")
        )


def test_execute_requires_an_execution_request(executor):
    with pytest.raises(SandboxConfigError, match="ExecutionRequest"):
        executor.execute({"category": "exec"})  # type: ignore[arg-type]


def test_per_category_profile_resolution(executor):
    assert executor.profile_for_category("network") == "standard"
    assert executor.profile_for_category("exec") == "restricted"
    assert executor.profile_for_category("docker") == "privileged"

    network_profile = executor.resolve_profile(
        ExecutionRequest(category="network", tool="network.fetch")
    )
    assert network_profile.name == "standard"
    assert network_profile.network_none is False


def test_explicit_profile_override_and_unknown_override_fails_closed(executor):
    privileged = executor.resolve_profile(
        ExecutionRequest(category="network", tool="x", profile_name="privileged")
    )
    assert privileged.name == "privileged"
    # Asking for a profile that does not exist must never widen the sandbox:
    # it resolves to the most secure profile.
    restricted = executor.resolve_profile(
        ExecutionRequest(category="docker", tool="x", profile_name="super-admin")
    )
    assert restricted.name == "restricted"


def test_execute_returns_canonical_result(executor):
    result = executor.execute(
        ExecutionRequest(category="network", tool="network.fetch", operation="http-get")
    )
    assert isinstance(result, ExecutionResult)
    assert result.accepted
    assert result.profile == "standard"
    assert result.category == "network"
    assert result.tool == "network.fetch"
    assert result.runtime == "offline"
    assert result.output["sandboxed"] is True


def test_audit_sink_receives_accepted_record(recording_sink):
    executor = SandboxExecutor(
        runtime=OfflineRuntime(enabled=True), audit=recording_sink
    )
    request = ExecutionRequest(
        category="network",
        tool="network.fetch",
        operation="http-get",
        tenant_id="acme",
        agent_id="agent-a",
        actor="subject-1",
    )
    executor.execute(request)
    assert len(recording_sink.events) == 1
    record = recording_sink.events[0]
    assert record["event"] == "tool_call"
    assert record["status"] == "ok"
    assert record["tenant_id"] == "acme"
    assert record["agent_id"] == "agent-a"
    assert record["actor"] == "subject-1"
    assert record["detail"]["outcome"] == "accepted"
    assert record["detail"]["profile"] == "standard"
    assert record["detail"]["category"] == "network"


def test_audit_sink_receives_denied_record(recording_sink):
    executor = SandboxExecutor(
        runtime=OfflineRuntime(enabled=True), audit=recording_sink
    )
    executor.execute(
        ExecutionRequest(
            category="exec",
            tool="exec.curl",
            operation="http-get",
            requires_network=True,
        )
    )
    record = recording_sink.events[0]
    assert record["event"] == "tool_call"
    assert record["status"] == "error"
    assert record["detail"]["outcome"] == "sandbox_denied"
    assert record["detail"]["profile"] == "restricted"
    assert "network access blocked" in record["detail"]["reason"]


def test_executor_without_audit_sink_runs(executor):
    result = executor.execute(
        ExecutionRequest(category="file", tool="file.read", operation="read")
    )
    assert result.accepted
