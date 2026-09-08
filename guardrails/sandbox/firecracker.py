"""Firecracker microVM executor: DECLARED, flag-gated OFF (schema + seam).

The optional Firecracker executor turns a security profile into a microVM
specification and would manage the microVM lifecycle (start / snapshot /
stop, the elevatedIQ ``internal/firecracker`` reference pattern). Per the IaC
doctrine and the issue #58 acceptance criteria it ships as a DECLARED seam
only: the spec schema (``microvm.schema.json`` + :class:`MicroVmSpec`), a
deterministic profile-to-limits mapping and an OFF-by-default runtime whose
``run`` refuses. No real Firecracker-go-sdk lifecycle is implemented - the seam
is where a deployment would wire it once its flag is on. Like every runtime,
nothing runs while ``enabled`` is False (fail closed).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from .errors import RuntimeExecutionError, RuntimeNotEnabledError, SandboxConfigError
from .model import ExecutionRequest, ExecutionResult, SecurityProfile
from .schema import load_schema, validate_document

_WIRE_TO_SNAKE = {
    "kernelPath": "kernel_path",
    "kernelArgs": "kernel_args",
    "rootfsPath": "rootfs_path",
    "vcpuCount": "vcpu_count",
    "memSizeMib": "mem_size_mib",
    "stateDir": "state_dir",
    "iface": "iface",
    "hostDevName": "host_dev_name",
    "jailer": "jailer",
}


@dataclass(frozen=True)
class MicroVmNetwork:
    """One guest network interface (absent network = None)."""

    iface: str
    host_dev_name: str

    def to_doc(self) -> Dict[str, Any]:
        return {"iface": self.iface, "hostDevName": self.host_dev_name}

    @classmethod
    def from_doc(cls, doc: Mapping[str, Any]) -> "MicroVmNetwork":
        return cls(iface=str(doc["iface"]), host_dev_name=str(doc["hostDevName"]))


@dataclass(frozen=True)
class MicroVmSpec:
    """A declared microVM: kernel + rootfs + resources + optional network.

    ``network=None`` means the microVM has no network interface; the jailer is
    optional. Serialisation uses the camelCase wire keys of
    ``microvm.schema.json``.
    """

    kernel_path: str
    kernel_args: str
    rootfs_path: str
    vcpu_count: int
    mem_size_mib: int
    state_dir: str
    network: Optional[MicroVmNetwork] = None
    jailer: Optional[str] = None

    def to_doc(self) -> Dict[str, Any]:
        doc: Dict[str, Any] = {
            "kernelPath": self.kernel_path,
            "kernelArgs": self.kernel_args,
            "rootfsPath": self.rootfs_path,
            "vcpuCount": self.vcpu_count,
            "memSizeMib": self.mem_size_mib,
            "stateDir": self.state_dir,
        }
        if self.network is not None:
            doc["network"] = self.network.to_doc()
        if self.jailer is not None:
            doc["jailer"] = self.jailer
        return doc

    @classmethod
    def from_doc(cls, doc: Mapping[str, Any]) -> "MicroVmSpec":
        if not isinstance(doc, Mapping):
            raise SandboxConfigError("microvm spec must be an object")
        errors = validate_document(doc, load_schema("microvm"))
        if errors:
            raise SandboxConfigError("microvm spec invalid: " + "; ".join(errors))
        network = None
        if doc.get("network") is not None:
            network = MicroVmNetwork.from_doc(doc["network"])
        return cls(
            kernel_path=str(doc["kernelPath"]),
            kernel_args=str(doc["kernelArgs"]),
            rootfs_path=str(doc["rootfsPath"]),
            vcpu_count=int(doc["vcpuCount"]),
            mem_size_mib=int(doc["memSizeMib"]),
            state_dir=str(doc["stateDir"]),
            network=network,
            jailer=str(doc["jailer"]) if doc.get("jailer") is not None else None,
        )


@dataclass(frozen=True)
class MicroVmHandle:
    """Opaque handle to a started microVM (returned by a real lifecycle)."""

    machine_id: str
    state_dir: str


def microvm_limits(profile: SecurityProfile) -> Dict[str, int]:
    """Deterministic microVM resource derivation from a profile.

    vCPU count is derived from the CFS CPU quota (rounded up to whole cores:
    50000/100000us -> 1 vCPU, 200000us -> 2 vCPUs) and memory from
    ``memory_mb``, so profiles.yaml drives the microVM sizing exactly as it
    drives the docker flags and the offline model.
    """
    return {
        "vcpuCount": max(1, int(math.ceil(profile.cpu_quota / 100000))),
        "memSizeMib": profile.memory_mb,
    }


class FirecrackerMicroVmRuntime:
    """Firecracker microVM isolation backend (declared, OFF by default).

    Implements the runtime seam (:meth:`run`) and the lifecycle seam
    (:meth:`start`) as refusals: while the flag is OFF nothing runs, and the
    real Firecracker lifecycle is not shipped (declared seam only, per the
    acceptance criteria). Tests prove the OFF-by-default refusal and the
    deterministic profile-to-microVM mapping.
    """

    name = "firecracker"

    def __init__(self, *, enabled: bool = False) -> None:
        self.enabled = bool(enabled)

    def run(
        self, request: ExecutionRequest, profile: SecurityProfile
    ) -> ExecutionResult:
        if not self.enabled:
            raise RuntimeNotEnabledError(
                "firecracker runtime is flag-gated OFF; no microVM runs until "
                "a deployment enables it behind its own IaC flag"
            )
        raise RuntimeExecutionError(
            "firecracker runtime is a declared seam: real microVM start/"
            "snapshot/stop is not shipped (schema + seam only)"
        )

    def start(self, spec: MicroVmSpec) -> MicroVmHandle:
        """Declared lifecycle seam - refuse until enabled, then unimplemented.

        A deployment that turns the flag on wires the real Firecracker
        lifecycle (the elevatedIQ ``internal/firecracker`` reference pattern)
        at this seam and returns a :class:`MicroVmHandle`.
        """
        if not self.enabled:
            raise RuntimeNotEnabledError(
                "firecracker runtime is flag-gated OFF; start() refuses"
            )
        raise RuntimeExecutionError(
            "firecracker start() is a declared seam; the real microVM "
            "lifecycle is not shipped"
        )
