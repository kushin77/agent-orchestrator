"""Sandbox error taxonomy.

One exception per failure surface so a caller can tell a policy denial apart
from a configuration error and from a disabled runtime - and so every path
fails closed: a denial is never turned into a silent success, and an
unavailable sandbox never degrades into an unsandboxed run.
"""

from __future__ import annotations


class SandboxError(Exception):
    """Base class for every sandbox failure."""


class SandboxDeniedError(SandboxError):
    """A tool execution was refused by the sandbox (profile enforcement).

    Raised by wiring layers (e.g. the MCP seam) when an execution request is
    not accepted so the denial travels up the caller's normal error path and
    is audited there.
    """


class SandboxDisabledError(SandboxError):
    """The sandbox is not enabled and refuses to execute anything.

    There is no code path that runs a tool without an enabled executor and an
    enabled runtime.
    """


class SandboxConfigError(SandboxError):
    """The sandbox contract (a profile/category/microVM document) is invalid.

    Loading a corrupt or non-conforming contract is a hard error, never a
    silent partial load or a silent fallback to a weaker profile.
    """


class RuntimeNotEnabledError(SandboxDisabledError):
    """A declared runtime (docker/firecracker) is flag-gated OFF.

    A runtime refuses to ``run`` while its ``enabled`` flag is False; a
    deployment turns the flag on only behind its own explicit IaC flag.
    """


class RuntimeExecutionError(SandboxError):
    """A real runtime failed to execute an already-accepted request.

    Distinct from a denial: the profile allowed the request but the underlying
    runtime (docker daemon, firecracker) could not carry it out.
    """
