"""Firecracker microVM executor tests: DECLARED seam, flag OFF, schema.

The optional Firecracker executor ships flag-gated OFF with a spec schema and
a deterministic profile-to-microVM limits mapping (no real lifecycle). These
tests prove the OFF-by-default refusal, the schema enforcement and the
mapping - everything the acceptance criterion requires of the seam.
"""

from __future__ import annotations

import pytest

from sandbox.errors import (
    RuntimeExecutionError,
    RuntimeNotEnabledError,
    SandboxConfigError,
)
from sandbox.executor import SandboxExecutor
from sandbox.firecracker import (
    FirecrackerMicroVmRuntime,
    MicroVmNetwork,
    MicroVmSpec,
    microvm_limits,
)
from sandbox.model import ExecutionRequest
from sandbox.schema import is_valid, load_schema, validate_document

GOOD_SPEC_DOC = {
    "kernelPath": "/opt/vmlinux.bin",
    "kernelArgs": "console=ttyS0 reboot=k panic=1 pci=off",
    "rootfsPath": "/opt/rootfs.ext4",
    "vcpuCount": 1,
    "memSizeMib": 512,
    "stateDir": "/run/firecracker/acme",
    "network": {"iface": "eth0", "hostDevName": "tap0"},
    "jailer": "/opt/jailer.bin",
}


def test_microvm_limits_derive_from_profiles(catalog):
    assert microvm_limits(catalog.profile("restricted")) == {
        "vcpuCount": 1,
        "memSizeMib": 512,
    }
    assert microvm_limits(catalog.profile("standard")) == {
        "vcpuCount": 1,
        "memSizeMib": 1024,
    }
    assert microvm_limits(catalog.profile("privileged")) == {
        "vcpuCount": 2,  # cpuQuota 200000us -> 2 whole cores
        "memSizeMib": 2048,
    }


def test_microvm_spec_from_doc_round_trip():
    spec = MicroVmSpec.from_doc(GOOD_SPEC_DOC)
    assert spec.to_doc() == GOOD_SPEC_DOC
    assert isinstance(spec.network, MicroVmNetwork)
    assert spec.network.iface == "eth0"
    assert spec.network.host_dev_name == "tap0"
    assert spec.jailer == "/opt/jailer.bin"


def test_microvm_spec_schema_validates_good_document():
    assert is_valid(GOOD_SPEC_DOC, load_schema("microvm"))


def test_microvm_spec_rejects_missing_required_fields():
    errors = validate_document({"kernelPath": "/x"}, load_schema("microvm"))
    assert errors and any("kernelArgs" in e for e in errors)


def test_microvm_spec_rejects_extra_key():
    bad = dict(GOOD_SPEC_DOC, balloonDevice=True)
    errors = validate_document(bad, load_schema("microvm"))
    assert errors and any("balloonDevice" in e for e in errors)


def test_microvm_spec_rejects_bad_resource_bounds():
    bad = dict(GOOD_SPEC_DOC, memSizeMib=4)  # below minimum 64
    errors = validate_document(bad, load_schema("microvm"))
    assert errors and any("memSizeMib" in e for e in errors)


def test_microvm_spec_from_doc_rejects_invalid_doc():
    with pytest.raises(SandboxConfigError):
        MicroVmSpec.from_doc({"kernelPath": "/x"})


def test_firecracker_runtime_ships_disabled_and_refuses_run():
    runtime = FirecrackerMicroVmRuntime()
    assert runtime.enabled is False
    with pytest.raises(RuntimeNotEnabledError, match="flag-gated OFF"):
        runtime.run(
            ExecutionRequest(category="exec", tool="exec.sh", operation="shell"),
            None,  # type: ignore[arg-type]  # never reached: refused first
        )
    with pytest.raises(RuntimeNotEnabledError, match="flag-gated OFF"):
        runtime.start(MicroVmSpec.from_doc(GOOD_SPEC_DOC))


def test_executor_with_off_firecracker_runtime_fails_closed():
    executor = SandboxExecutor(runtime=FirecrackerMicroVmRuntime())
    with pytest.raises(RuntimeNotEnabledError):
        executor.execute(
            ExecutionRequest(category="exec", tool="exec.sh", operation="shell")
        )


def test_enabled_firecracker_runtime_is_a_declared_seam(catalog):
    # Enabling the flag does not fake a microVM: the lifecycle is a declared
    # seam, so run/start raise an explicit not-implemented error (honest).
    runtime = FirecrackerMicroVmRuntime(enabled=True)
    assert runtime.enabled is True
    with pytest.raises(RuntimeExecutionError, match="declared seam"):
        runtime.run(
            ExecutionRequest(category="exec", tool="exec.sh", operation="shell"),
            catalog.profile("restricted"),
        )
    with pytest.raises(RuntimeExecutionError, match="declared seam"):
        runtime.start(MicroVmSpec.from_doc(GOOD_SPEC_DOC))
