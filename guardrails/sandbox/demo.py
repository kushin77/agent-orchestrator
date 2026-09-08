#!/usr/bin/env python3
"""Offline demo + self-check for the agent tool-execution sandbox (issue #58).

Runs the executor through the per-category profile defaults, the fail-closed
unknown-category path and the restricted-profile negatives against the offline
runtime, then prints a PASS line. Every failed assertion raises, so the demo
exits nonzero on any failure - it cannot silently pass (no-false-green).
"""

from __future__ import annotations

import os
import sys

# guardrails/ must be importable so this package resolves as `sandbox`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sandbox import docker as docker_mod  # noqa: E402
from sandbox.catalog import default_category_map  # noqa: E402
from sandbox.errors import SandboxDisabledError  # noqa: E402
from sandbox.executor import SandboxExecutor  # noqa: E402
from sandbox.model import ExecutionRequest  # noqa: E402
from sandbox.offline import OfflineRuntime  # noqa: E402


def main() -> int:
    checks = 0

    def check(condition: bool, label: str) -> None:
        nonlocal checks
        if not condition:
            raise AssertionError(f"FAIL: {label}")
        checks += 1
        print(f"  ok - {label}")

    executor = SandboxExecutor(runtime=OfflineRuntime(enabled=True))
    category_map = default_category_map()

    print("== per-category profile defaults ==")
    check(
        executor.profile_for_category("file") == "restricted",
        "file category -> restricted",
    )
    check(
        executor.profile_for_category("exec") == "restricted",
        "exec category -> restricted",
    )
    check(
        executor.profile_for_category("network") == "standard",
        "network category -> standard",
    )
    check(
        executor.profile_for_category("docker") == "privileged",
        "docker category -> privileged",
    )
    check(
        executor.profile_for_category("k8s") == "standard",
        "k8s category -> standard",
    )

    print("== fail-closed unknown category ==")
    check(
        category_map.profile_for("scheduler") == "restricted",
        "unknown category resolves to the fail-closed default (restricted)",
    )
    denied_unknown = executor.execute(
        ExecutionRequest(
            category="scheduler",
            tool="scheduler.submit",
            operation="submit",
            requires_network=True,
        )
    )
    check(
        denied_unknown.denied and denied_unknown.profile == "restricted",
        "unknown-category network op is denied under the restricted profile",
    )
    check(
        "network access blocked" in denied_unknown.reason,
        "denial names the enforced axis (network=none)",
    )

    print("== restricted profile negatives ==")
    denied_net = executor.execute(
        ExecutionRequest(
            category="exec",
            tool="exec.curl",
            operation="http-get",
            requires_network=True,
        )
    )
    check(
        denied_net.denied and "network access blocked" in denied_net.reason,
        "restricted profile blocks a network tool call",
    )
    denied_cap = executor.execute(
        ExecutionRequest(
            category="exec",
            tool="exec.mount",
            operation="mount",
            requires_caps=("SYS_ADMIN",),
        )
    )
    check(
        denied_cap.denied
        and "SYS_ADMIN" in denied_cap.reason
        and "dropped" in denied_cap.reason,
        "restricted profile blocks capability escalation (capDrop ALL)",
    )
    denied_mem = executor.execute(
        ExecutionRequest(
            category="file",
            tool="file.ingest",
            operation="ingest",
            requires_rootfs_write=True,
        )
    )
    check(
        denied_mem.denied and "rootfs write" in denied_mem.reason,
        "restricted profile blocks a rootfs write (read-only rootfs)",
    )

    print("== positive controls (same ops under the right profile) ==")
    accepted_net = executor.execute(
        ExecutionRequest(
            category="network",
            tool="network.fetch",
            operation="http-get",
            requires_network=True,
        )
    )
    check(
        accepted_net.accepted and accepted_net.profile == "standard",
        "the same network op is accepted under standard (bridge network)",
    )
    accepted_docker = executor.execute(
        ExecutionRequest(
            category="docker",
            tool="docker.run",
            operation="run",
            requires_network=True,
            requires_caps=("NET_ADMIN",),
            requires_rootfs_write=True,
        )
    )
    check(
        accepted_docker.accepted and accepted_docker.profile == "privileged",
        "the same op is accepted under privileged (host network, writable)",
    )

    print("== docker flag mapping (declared runtime, pure mapping) ==")
    restricted_flags = docker_mod.docker_run_flags(executor.catalog.profile("restricted"))
    check(
        "--read-only" in restricted_flags
        and "--cap-drop=ALL" in restricted_flags
        and "--network=none" in restricted_flags,
        "restricted profile maps to read-only + cap-drop ALL + network none",
    )
    privileged_flags = docker_mod.docker_run_flags(executor.catalog.profile("privileged"))
    check(
        "--read-only" not in privileged_flags
        and "--cap-drop=SYS_ADMIN" in privileged_flags
        and "--network=host" in privileged_flags,
        "privileged profile maps to writable + partial cap-drop + host network",
    )

    print("== flag-gated OFF runtimes refuse (fail closed) ==")
    docker_off = docker_mod.DockerRuntime()  # enabled defaults to False
    off_executor = SandboxExecutor(runtime=docker_off)
    try:
        off_executor.execute(
            ExecutionRequest(category="exec", tool="exec.sh", operation="shell")
        )
        raise AssertionError("FAIL: disabled docker runtime executed a tool")
    except SandboxDisabledError as exc:
        check("flag-gated OFF" in str(exc), "a flag-gated-OFF runtime refuses to run")

    print(f"\nsandbox demo: OK (all {checks} assertions passed)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
