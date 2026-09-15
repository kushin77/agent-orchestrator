"""Sandbox runtime enablement: flag-gated OFF, enable/disable round-trip (#636).

The acceptance criterion is that the sandbox runtime enablement path (docker /
firecracker) is gated behind a feature flag that defaults OFF, and that the
enable/disable round-trip is **proven on the injectable runtime** — no docker
daemon, no Firecracker, no network. Every test here runs against
``OfflineRuntime`` (the injectable fake) or against a declared runtime's
refusal.

The negative controls are the point:

* a flag constructed without ``enable()`` is OFF, and ``assert_not_auto_on``
  fails loudly if that stops being true;
* enabling a *declared* runtime (docker/firecracker) does NOT fabricate an
  execution — it still refuses, so "flag on" can never be mistaken for
  "sandboxed";
* disabling genuinely re-arms the refusal.
"""

from __future__ import annotations

import pytest

from sandbox import (
    DECLARED_RUNTIMES,
    RUNTIME_NAMES,
    DockerRuntime,
    EnablementError,
    FirecrackerMicroVmRuntime,
    OfflineRuntime,
    SandboxEnablement,
    SandboxExecutor,
    SandboxExecutor as _Executor,
    assert_not_auto_on,
    default_enablement,
)
from sandbox.errors import RuntimeExecutionError
from sandbox.model import ExecutionRequest

_ALL_RUNTIMES = pytest.mark.parametrize("runtime_name", RUNTIME_NAMES)


def _request(**kwargs) -> ExecutionRequest:
    base = {"category": "exec", "tool": "exec.probe", "operation": "probe"}
    base.update(kwargs)
    return ExecutionRequest(**base)


# --------------------------------------------------------------------------- #
# the flag ships OFF and never auto-enables
# --------------------------------------------------------------------------- #
@_ALL_RUNTIMES
def test_a_fresh_flag_is_off_for_every_runtime(runtime_name):
    flag = default_enablement(runtime_name)
    assert flag.is_enabled is False
    assert flag.runtime is None
    assert flag.activations() == 0


@_ALL_RUNTIMES
def test_assert_not_auto_on_passes_for_a_fresh_flag(runtime_name):
    assert_not_auto_on(runtime_name)


def test_the_negative_control_fails_when_the_flag_defaults_on():
    """If the flag could ever be built ON, the probe must raise (not pass)."""

    def on_by_default(name: str) -> SandboxEnablement:
        flag = SandboxEnablement(name=name)
        flag.enabled = True  # simulate the regression the probe exists to catch
        return flag

    with pytest.raises(AssertionError, match="default-OFF is not enforced"):
        assert_not_auto_on("docker", factory=on_by_default)


def test_constructing_an_already_on_flag_is_refused():
    with pytest.raises(EnablementError, match="already ON"):
        SandboxEnablement(name="docker", enabled=True)


def test_constructing_a_flag_with_a_live_runtime_is_refused():
    with pytest.raises(EnablementError, match="live runtime"):
        SandboxEnablement(name="docker", runtime=OfflineRuntime(enabled=True))


def test_an_unknown_runtime_name_is_refused():
    from sandbox.errors import SandboxConfigError

    with pytest.raises(SandboxConfigError, match="unknown sandbox runtime"):
        SandboxEnablement(name="podman")


def test_require_before_enable_is_a_hard_error():
    flag = default_enablement("offline")
    with pytest.raises(EnablementError, match="not enabled"):
        flag.require()


# --------------------------------------------------------------------------- #
# the enable/disable round-trip, proven on the injectable runtime
# --------------------------------------------------------------------------- #
def test_enable_disable_round_trip_on_the_injectable_runtime():
    flag = default_enablement("offline")
    assert flag.is_enabled is False and flag.runtime is None

    # OFF -> the executor refuses (fail closed, no unsandboxed run).
    executor = SandboxExecutor(runtime=OfflineRuntime(enabled=False))
    with pytest.raises(Exception, match="flag-gated OFF"):
        executor.execute(_request())

    # ENABLE -> a live, enabled offline runtime, and the same call succeeds.
    runtime = flag.enable()
    assert isinstance(runtime, OfflineRuntime)
    assert flag.is_enabled is True
    assert runtime.enabled is True
    assert flag.activations() == 1

    ok_executor = SandboxExecutor(runtime=flag.require())
    result = ok_executor.execute(_request())
    assert result.accepted is True
    assert result.runtime == "offline"

    # DISABLE -> back to the shipped posture; the runtime refuses again.
    flag.disable()
    assert flag.is_enabled is False
    assert flag.runtime is None
    assert runtime.enabled is False
    with pytest.raises(Exception, match="flag-gated OFF"):
        SandboxExecutor(runtime=runtime).execute(_request())


def test_disable_then_enable_again_is_idempotent_and_audits_activations():
    flag = default_enablement("offline")
    first = flag.enable()
    assert flag.enable() is first, "enabling twice must not build a second runtime"
    assert flag.activations() == 1
    flag.disable()
    second = flag.enable()
    assert second is not first
    assert flag.activations() == 2


def test_disable_is_idempotent():
    flag = default_enablement("offline")
    flag.disable()
    flag.disable()
    assert flag.is_enabled is False and flag.runtime is None


# --------------------------------------------------------------------------- #
# declared runtimes: enabling the flag is NOT sandboxing
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("runtime_name", DECLARED_RUNTIMES)
def test_enabling_a_declared_runtime_still_refuses_to_fabricate_a_run(runtime_name):
    flag = default_enablement(runtime_name)
    assert flag.is_declared is True
    runtime = flag.enable()
    assert flag.is_enabled is True and runtime.enabled is True
    # Enabled, but still a declared seam: no fabricated success.
    with pytest.raises(RuntimeExecutionError, match="declared seam"):
        runtime.run(_request(), SandboxExecutor(runtime=runtime).resolve_profile(_request()))


def test_the_declared_runtimes_are_the_expected_pair():
    assert DECLARED_RUNTIMES == ("docker", "firecracker")
    assert isinstance(default_enablement("docker").enable(), DockerRuntime)
    assert isinstance(
        default_enablement("firecracker").enable(), FirecrackerMicroVmRuntime
    )


# --------------------------------------------------------------------------- #
# no env var can flip the flag behind a reviewer's back
# --------------------------------------------------------------------------- #
def test_no_environment_variable_auto_enables_the_sandbox(monkeypatch):
    for name in (
        "AO_SANDBOX_ENABLED",
        "AO_SANDBOX_RUNTIME_ENABLED",
        "SANDBOX_ENABLED",
        "SANDBOX_RUNTIME",
    ):
        monkeypatch.setenv(name, "1")
    flag = default_enablement("docker")
    assert flag.is_enabled is False and flag.runtime is None
    assert_not_auto_on("docker")
