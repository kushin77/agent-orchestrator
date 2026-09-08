"""Security-profile and tool-category model for the agent tool-execution sandbox.

The isolation contract is adapted (not copied) from the elevatedIQ
``services/ai-chatbot-orchestrator/internal/sandbox`` pattern (spike #48,
issue #58): every agent tool call runs under one of three security profiles -
``restricted`` / ``standard`` / ``privileged`` - that carry the isolation
knobs that matter (read-only rootfs, no-new-privileges, dropped Linux
capabilities, network mode, run user, and CPU / memory / PID / timeout
quotas). A closed category map binds the agent tool categories
(file/exec/network/db/cloud/git/docker/k8s) to a profile and fails closed to
``restricted`` for any undeclared category.

The wire contract is declared as JSON Schema (``*.schema.json``) and
instantiated as YAML (``profiles.yaml``, ``categories.yaml``); this module is
the typed, stdlib-only Python view of that contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, Mapping, Optional, Tuple

from .errors import SandboxConfigError

# Closed network modes a profile may declare (the network-isolation axis).
NETWORK_NONE = "none"
NETWORK_BRIDGE = "bridge"
NETWORK_HOST = "host"
NETWORK_MODES: Tuple[str, ...] = (NETWORK_NONE, NETWORK_BRIDGE, NETWORK_HOST)

# Closed profile vocabulary (the elevatedIQ restricted/standard/privileged set).
PROFILE_NAMES: Tuple[str, ...] = ("restricted", "standard", "privileged")
DEFAULT_PROFILE = "restricted"

# Closed agent tool-category vocabulary bound by categories.yaml (elevatedIQ
# CategoryProfiles keys). Categories outside this set are still executable
# fail-closed - they resolve to the map's default profile.
TOOL_CATEGORIES: Tuple[str, ...] = (
    "file",
    "exec",
    "network",
    "db",
    "cloud",
    "git",
    "docker",
    "k8s",
)

# camelCase (wire/schema) <-> snake_case (python) profile field mapping. The
# wire names match the JSON Schema properties and the elevatedIQ Go struct
# tags; the python dataclass uses snake_case.
_FIELDS: Tuple[Tuple[str, str], ...] = (
    ("readOnlyRootfs", "read_only_rootfs"),
    ("noNewPrivs", "no_new_privs"),
    ("capDrop", "cap_drop"),
    ("networkMode", "network_mode"),
    ("user", "user"),
    ("workDir", "work_dir"),
    ("cpuQuota", "cpu_quota"),
    ("memoryMb", "memory_mb"),
    ("pidsLimit", "pids_limit"),
    ("timeoutSeconds", "timeout_seconds"),
)
_WIRE_KEYS = frozenset(wire for wire, _ in _FIELDS)

# The special capDrop value meaning "every Linux capability is dropped".
CAP_ALL = "ALL"


@dataclass(frozen=True)
class SecurityProfile:
    """A resolved security profile (restricted / standard / privileged).

    ``cpu_quota`` is the Linux CFS CPU quota in microseconds per 100 ms period
    (50000 = 50% of one core); ``memory_mb``, ``pids_limit`` and
    ``timeout_seconds`` are self-describing. ``cap_drop`` lists Linux
    capabilities dropped from the container/microVM; ``CAP_ALL`` drops all of
    them. ``network_mode`` is one of ``NETWORK_MODES``.
    """

    name: str
    read_only_rootfs: bool
    no_new_privs: bool
    cap_drop: Tuple[str, ...]
    network_mode: str
    user: str
    work_dir: str
    cpu_quota: int
    memory_mb: int
    pids_limit: int
    timeout_seconds: int

    def __post_init__(self) -> None:
        if self.network_mode not in NETWORK_MODES:
            raise SandboxConfigError(
                f"profile {self.name!r}: invalid networkMode "
                f"{self.network_mode!r} (closed set: {NETWORK_MODES})"
            )
        if self.cpu_quota < 0 or self.memory_mb < 1 or self.pids_limit < 1:
            raise SandboxConfigError(
                f"profile {self.name!r}: resource quotas must be positive "
                f"(cpu_quota={self.cpu_quota}, memory_mb={self.memory_mb}, "
                f"pids_limit={self.pids_limit})"
            )
        if self.timeout_seconds < 1:
            raise SandboxConfigError(
                f"profile {self.name!r}: timeout_seconds must be positive "
                f"(got {self.timeout_seconds})"
            )
        object.__setattr__(self, "cap_drop", tuple(self.cap_drop))

    # ------------------------------------------------------------------ #
    # semantic helpers used by runtimes to enforce the profile
    # ------------------------------------------------------------------ #
    @property
    def network_none(self) -> bool:
        """True when the profile declares no network access."""
        return self.network_mode == NETWORK_NONE

    def drops_all_caps(self) -> bool:
        """True when every Linux capability is dropped (capDrop ALL)."""
        return CAP_ALL in self.cap_drop

    def drops_cap(self, cap: str) -> bool:
        """Whether the profile drops ``cap`` (or drops all capabilities)."""
        return self.drops_all_caps() or cap in self.cap_drop

    # ------------------------------------------------------------------ #
    # wire (de)serialisation - camelCase keys matching the JSON Schema
    # ------------------------------------------------------------------ #
    @classmethod
    def from_doc(cls, name: str, doc: Mapping[str, Any]) -> "SecurityProfile":
        """Build a profile from its wire document (camelCase keys).

        Unknown keys, missing keys, a bad network mode and non-positive
        quotas are hard :class:`SandboxConfigError` failures (fail closed).
        """
        if not isinstance(doc, Mapping):
            raise SandboxConfigError(
                f"profile {name!r}: expected an object, got {type(doc).__name__}"
            )
        extra = sorted(str(key) for key in doc if key not in _WIRE_KEYS)
        if extra:
            raise SandboxConfigError(
                f"profile {name!r}: unknown field(s) {extra} "
                f"(closed set: {sorted(_WIRE_KEYS)})"
            )
        missing = [wire for wire, _ in _FIELDS if wire not in doc]
        if missing:
            raise SandboxConfigError(
                f"profile {name!r}: missing required field(s) {missing}"
            )
        try:
            profile = cls(
                name=name,
                read_only_rootfs=bool(doc["readOnlyRootfs"]),
                no_new_privs=bool(doc["noNewPrivs"]),
                cap_drop=tuple(str(cap) for cap in doc["capDrop"]),
                network_mode=str(doc["networkMode"]),
                user=str(doc["user"]),
                work_dir=str(doc["workDir"]),
                cpu_quota=int(doc["cpuQuota"]),
                memory_mb=int(doc["memoryMb"]),
                pids_limit=int(doc["pidsLimit"]),
                timeout_seconds=int(doc["timeoutSeconds"]),
            )
        except (TypeError, ValueError) as exc:
            raise SandboxConfigError(
                f"profile {name!r}: malformed value(s): {exc}"
            ) from exc
        return profile

    def to_doc(self) -> Dict[str, Any]:
        """Wire view of the profile (camelCase keys, JSON-safe values)."""
        return {
            "readOnlyRootfs": self.read_only_rootfs,
            "noNewPrivs": self.no_new_privs,
            "capDrop": list(self.cap_drop),
            "networkMode": self.network_mode,
            "user": self.user,
            "workDir": self.work_dir,
            "cpuQuota": self.cpu_quota,
            "memoryMb": self.memory_mb,
            "pidsLimit": self.pids_limit,
            "timeoutSeconds": self.timeout_seconds,
        }


@dataclass(frozen=True)
class ExecutionRequest:
    """What a tool wants to do and what it needs from the sandbox.

    ``category`` is the tool category (file/exec/network/db/cloud/git/docker/
    k8s, or anything else - undeclared categories fail closed to restricted).
    The requirement fields express the operation's intent so a runtime can
    enforce the profile's isolation axes offline and in the real sandbox:
    network access, Linux capabilities, rootfs writes, privilege escalation,
    and resource ceilings. ``profile_name`` is an optional explicit override;
    when absent the executor uses the per-category default. ``tenant_id`` /
    ``agent_id`` / ``actor`` are audit context consumed when an audit sink is
    wired (the who of the tool call, mcp-shaped).
    """

    category: str
    tool: str
    operation: str = ""
    args: Mapping[str, Any] = field(default_factory=dict)
    profile_name: Optional[str] = None
    requires_network: bool = False
    requires_caps: Tuple[str, ...] = ()
    requires_rootfs_write: bool = False
    requires_privilege_escalation: bool = False
    requested_cpu_quota: Optional[int] = None
    requested_memory_mb: Optional[int] = None
    requested_pids: Optional[int] = None
    requested_timeout_seconds: Optional[float] = None
    tenant_id: Optional[str] = None
    agent_id: Optional[str] = None
    actor: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "args", dict(self.args))
        object.__setattr__(self, "requires_caps", tuple(self.requires_caps))

    def with_identity(
        self, *, tenant_id: Optional[str], agent_id: Optional[str],
        actor: Optional[str],
    ) -> "ExecutionRequest":
        """Return a copy carrying the audit identity of the caller."""
        return replace(
            self, tenant_id=tenant_id, agent_id=agent_id, actor=actor
        )


@dataclass(frozen=True)
class ExecutionResult:
    """The outcome of a sandboxed execution.

    ``accepted`` is False only when the runtime refused the request because of
    the resolved profile (a denial); the ``reason`` then names the enforced
    axis. ``output`` holds the runtime's result for accepted executions.
    """

    accepted: bool
    reason: str = ""
    profile: str = DEFAULT_PROFILE
    category: str = ""
    tool: str = ""
    runtime: str = ""
    output: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "output", dict(self.output))

    @property
    def denied(self) -> bool:
        return not self.accepted
