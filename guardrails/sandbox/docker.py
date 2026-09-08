"""Docker runtime: DECLARED and flag-gated OFF (IaC doctrine).

Maps a security profile to the docker(1) isolation flags that realise it and
would run the tool inside a container. The runtime ships DISABLED: constructing
one never starts anything, and ``run`` refuses while ``enabled`` is False
(fail closed). A deployment turns the flag on only behind its own explicit IaC
flag and only where a docker daemon exists. ``docker_run_flags`` is pure and
offline so the profile-to-flags mapping is unit-tested without a daemon; the
profile values it consumes come from ``profiles.yaml`` (restricted/standard/
privileged), adapted from the elevatedIQ sandbox pattern (spike #48, #58).
"""

from __future__ import annotations

from typing import List

from .errors import RuntimeExecutionError, RuntimeNotEnabledError
from .model import ExecutionRequest, ExecutionResult, SecurityProfile


def docker_run_flags(profile: SecurityProfile) -> List[str]:
    """The docker(1) flags that realise ``profile`` (deterministic order).

    The mapping is 1:1 with the profile fields: read-only rootfs, no-new-privs,
    capability drops, network mode, run user, workdir and the CPU / memory /
    PID quotas. The timeout is enforced by the caller (docker(1) has no run
    timeout) and is deliberately not expressed as a flag here.
    """
    flags: List[str] = ["run", "--rm"]

    if profile.read_only_rootfs:
        flags.append("--read-only")

    if profile.no_new_privs:
        flags.append("--security-opt=no-new-privileges")

    if profile.drops_all_caps():
        flags.append("--cap-drop=ALL")
    else:
        flags.extend(f"--cap-drop={cap}" for cap in profile.cap_drop)

    flags.append(f"--network={profile.network_mode}")

    if profile.user:
        flags.append(f"--user={profile.user}")

    flags.append(f"--workdir={profile.work_dir}")
    flags.append(f"--cpu-quota={profile.cpu_quota}")
    flags.append(f"--memory={profile.memory_mb}m")
    flags.append(f"--pids-limit={profile.pids_limit}")

    return flags


class DockerRuntime:
    """Docker container isolation backend (declared, OFF by default).

    ``run`` refuses while disabled; when a deployment enables it, the seam is
    where real ``docker run`` isolation happens. This repository is offline and
    never invokes docker - tests exercise the pure flag mapping and the
    OFF-by-default refusal only.
    """

    name = "docker"

    def __init__(self, *, enabled: bool = False) -> None:
        self.enabled = bool(enabled)

    def run(
        self, request: ExecutionRequest, profile: SecurityProfile
    ) -> ExecutionResult:
        if not self.enabled:
            raise RuntimeNotEnabledError(
                "docker runtime is flag-gated OFF; no container runs until a "
                "deployment enables it behind its own IaC flag"
            )
        raise RuntimeExecutionError(
            "docker runtime is a declared seam: real container isolation is "
            "wired by the deployment owner behind its IaC flag; this offline "
            "repository never invokes the docker daemon"
        )
