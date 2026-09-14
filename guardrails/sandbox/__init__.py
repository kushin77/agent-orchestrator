"""Agent tool-execution sandbox package (guardrails pillar, issue #58).

Every agent tool call (file/exec/network/db/cloud/git/docker/k8s) runs under a
security profile selected per tool category - restricted / standard /
privileged - with a fail-closed default to restricted for unknown categories.
The contract is declared as JSON Schema and instantiated as YAML
(``profiles.yaml``, ``categories.yaml``); a :class:`SandboxExecutor` resolves
the per-category profile and dispatches to an injectable runtime (an offline
enforcement model in tests; real Docker/Firecracker runtimes are DECLARED
behind a flag that ships OFF). An additive seam wires the sandbox into the MCP
tool gateway (gateway/mcp, issue #20) so tool Execute routes through the
sandbox and is audited.

Everything here is offline (stdlib + PyYAML), lives strictly under
``guardrails/sandbox/`` and is adapted - not copied - from the elevatedIQ
ai-chatbot-orchestrator sandbox pattern (spike #48).
"""

from __future__ import annotations

from .catalog import (
    CategoryMap,
    ProfileCatalog,
    default_category_map,
    default_profile_catalog,
    load_category_map,
    load_profile_catalog,
    resolve_profile,
    resolve_profile_for_category,
)
from .docker import DockerRuntime, docker_run_flags
from .enablement import (
    DECLARED_RUNTIMES,
    RUNTIME_NAMES,
    EnablementError,
    SandboxEnablement,
    assert_not_auto_on,
    default_enablement,
)
from .errors import (
    RuntimeExecutionError,
    RuntimeNotEnabledError,
    SandboxConfigError,
    SandboxDeniedError,
    SandboxDisabledError,
    SandboxError,
)
from .executor import AuditSink, SandboxExecutor
from .firecracker import (
    FirecrackerMicroVmRuntime,
    MicroVmHandle,
    MicroVmNetwork,
    MicroVmSpec,
    microvm_limits,
)
from .model import (
    CAP_ALL,
    DEFAULT_PROFILE,
    NETWORK_BRIDGE,
    NETWORK_HOST,
    NETWORK_MODES,
    NETWORK_NONE,
    PROFILE_NAMES,
    TOOL_CATEGORIES,
    ExecutionRequest,
    ExecutionResult,
    SecurityProfile,
)
from .offline import OfflineRuntime
from .runtime import Runtime

__all__ = [
    "AuditSink",
    "CAP_ALL",
    "CategoryMap",
    "DECLARED_RUNTIMES",
    "DEFAULT_PROFILE",
    "DockerRuntime",
    "EnablementError",
    "ExecutionRequest",
    "ExecutionResult",
    "FirecrackerMicroVmRuntime",
    "MicroVmHandle",
    "MicroVmNetwork",
    "MicroVmSpec",
    "NETWORK_BRIDGE",
    "NETWORK_HOST",
    "NETWORK_MODES",
    "NETWORK_NONE",
    "OfflineRuntime",
    "PROFILE_NAMES",
    "ProfileCatalog",
    "RUNTIME_NAMES",
    "Runtime",
    "RuntimeExecutionError",
    "RuntimeNotEnabledError",
    "SandboxConfigError",
    "SandboxDeniedError",
    "SandboxDisabledError",
    "SandboxEnablement",
    "SandboxError",
    "SandboxExecutor",
    "SecurityProfile",
    "TOOL_CATEGORIES",
    "assert_not_auto_on",
    "default_category_map",
    "default_enablement",
    "default_profile_catalog",
    "docker_run_flags",
    "load_category_map",
    "load_profile_catalog",
    "microvm_limits",
    "resolve_profile",
    "resolve_profile_for_category",
]
