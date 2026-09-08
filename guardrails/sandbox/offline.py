"""Offline enforcement model: deterministic, profile-derived sandbox checks.

The OfflineRuntime models, in-process, the isolation a real Docker/Firecracker
runtime would enforce. Every decision is derived FROM THE PROFILE FIELDS - not
from a parallel table - so editing ``profiles.yaml`` changes enforcement here:

* ``networkMode: none`` denies any request that requires network access;
* a dropped capability (or ``capDrop: [ALL]``) denies a request that asks for
  it (capability escalation);
* ``noNewPrivs: true`` denies a request that needs privilege escalation;
* ``readOnlyRootfs: true`` denies a request that must write the rootfs;
* the CPU / memory / PID / timeout quotas are enforced as upper bounds on what
  an execution may ask for.

An accepted request returns a deterministic simulated completion. This is NOT a
real sandbox - it exists so the executor seam, the per-category profile
defaults and the fail-closed rules are fully testable offline and in CI with no
docker daemon and no network. Like every runtime it refuses to run while
``enabled`` is False.
"""

from __future__ import annotations

from typing import List

from .errors import RuntimeNotEnabledError
from .model import ExecutionRequest, ExecutionResult, SecurityProfile


class OfflineRuntime:
    """The injectable offline/fake runtime (tests + offline demos + CI)."""

    name = "offline"

    def __init__(self, *, enabled: bool = True) -> None:
        # Tests enable the fake explicitly; keeping the switch honest means the
        # OFF-by-default behaviour of the real runtimes is also provable here.
        self.enabled = bool(enabled)

    def _enforce(self, request: ExecutionRequest, profile: SecurityProfile) -> List[str]:
        """Return every profile axis the request violates (empty = allowed)."""
        denied: List[str] = []

        if request.requires_network and profile.network_none:
            denied.append(
                f"network access blocked by profile {profile.name!r} "
                f"(networkMode=none)"
            )

        for cap in request.requires_caps:
            if profile.drops_cap(cap):
                denied.append(
                    f"capability {cap!r} is dropped by profile {profile.name!r} "
                    f"(capDrop={list(profile.cap_drop)})"
                )

        if request.requires_privilege_escalation and profile.no_new_privs:
            denied.append(
                f"privilege escalation refused by profile {profile.name!r} "
                f"(noNewPrivs=true)"
            )

        if request.requires_rootfs_write and profile.read_only_rootfs:
            denied.append(
                f"rootfs write refused by profile {profile.name!r} "
                f"(readOnlyRootfs=true)"
            )

        if (
            request.requested_cpu_quota is not None
            and request.requested_cpu_quota > profile.cpu_quota
        ):
            denied.append(
                f"cpu quota {request.requested_cpu_quota}us exceeds profile "
                f"{profile.name!r} ceiling {profile.cpu_quota}us"
            )

        if (
            request.requested_memory_mb is not None
            and request.requested_memory_mb > profile.memory_mb
        ):
            denied.append(
                f"memory {request.requested_memory_mb}MiB exceeds profile "
                f"{profile.name!r} ceiling {profile.memory_mb}MiB"
            )

        if (
            request.requested_pids is not None
            and request.requested_pids > profile.pids_limit
        ):
            denied.append(
                f"pids {request.requested_pids} exceeds profile "
                f"{profile.name!r} limit {profile.pids_limit}"
            )

        if (
            request.requested_timeout_seconds is not None
            and request.requested_timeout_seconds > profile.timeout_seconds
        ):
            denied.append(
                f"timeout {request.requested_timeout_seconds}s exceeds profile "
                f"{profile.name!r} limit {profile.timeout_seconds}s"
            )

        return denied

    def run(
        self, request: ExecutionRequest, profile: SecurityProfile
    ) -> ExecutionResult:
        if not self.enabled:
            raise RuntimeNotEnabledError(
                f"offline runtime is not enabled; refusing {request.tool!r}"
            )

        violations = self._enforce(request, profile)
        if violations:
            return ExecutionResult(
                accepted=False,
                reason="; ".join(violations),
                profile=profile.name,
                runtime=self.name,
            )

        return ExecutionResult(
            accepted=True,
            reason="",
            profile=profile.name,
            runtime=self.name,
            output={
                "sandboxed": True,
                "runtime": self.name,
                "profile": profile.name,
                "category": request.category,
                "tool": request.tool,
                "operation": request.operation,
                "exitCode": 0,
            },
        )
