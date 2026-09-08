"""Sandbox executor: per-category profile defaults, fail-closed dispatch.

The executor is the decision surface of the tool-execution sandbox. For an
:class:`~sandbox.model.ExecutionRequest` it:

1. refuses unless the executor itself is enabled (no unsandboxed execution);
2. resolves the security profile - an explicit ``profile_name`` override, else
   the per-category default from the category map, else the map's fail-closed
   default (``restricted``) for an undeclared/unknown category;
3. refuses unless the injected runtime is enabled (flag-gated OFF doctrine);
4. dispatches to the injected runtime, which enforces the profile's isolation
   axes;
5. writes one execution record to the optional audit sink (mcp-shaped,
   consumed contract) so a sandbox wired outside the MCP gateway still leaves
   an append-only who/what/tenant/result trace.

The executor never executes a tool itself and never runs a tool without an
enabled runtime; every refusal is a hard error or a denied result - there is
no code path that turns a denial into a silent success.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, Optional, Protocol

from .catalog import (
    CategoryMap,
    ProfileCatalog,
    default_category_map,
    default_profile_catalog,
    resolve_profile,
)
from .errors import (
    RuntimeNotEnabledError,
    SandboxConfigError,
    SandboxDisabledError,
)
from .model import ExecutionRequest, ExecutionResult, SecurityProfile
from .runtime import Runtime


class AuditSink(Protocol):
    """The optional audit ledger (mcp-shaped append-only contract, consumed).

    Matches the gateway/mcp ``AuditSink`` append signature (issue #20): the
    sandbox writes ``tool_call`` records with status ``ok``/``error`` and a
    ``detail`` carrying tool/category/profile/outcome, so the same ledger shape
    is shared rather than redefined.
    """

    def append(
        self,
        event: str,
        *,
        status: Optional[str] = None,
        ts: Optional[str] = None,
        tenant_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        actor: Optional[str] = None,
        detail: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]: ...


class SandboxExecutor:
    """Per-category, fail-closed tool-execution sandbox decision surface."""

    def __init__(
        self,
        *,
        catalog: Optional[ProfileCatalog] = None,
        category_map: Optional[CategoryMap] = None,
        runtime: Optional[Runtime] = None,
        audit: Optional[AuditSink] = None,
        enabled: bool = True,
    ) -> None:
        if runtime is None:
            raise SandboxConfigError(
                "a runtime must be injected (offline fake in tests; real "
                "docker/firecracker behind a flag that ships OFF)"
            )
        self._catalog = catalog or default_profile_catalog()
        self._category_map = category_map or default_category_map()
        self._runtime = runtime
        self._audit = audit
        self.enabled = bool(enabled)

    # ------------------------------------------------------------------ #
    # accessors
    # ------------------------------------------------------------------ #
    @property
    def runtime(self) -> Runtime:
        return self._runtime

    @property
    def catalog(self) -> ProfileCatalog:
        return self._catalog

    @property
    def category_map(self) -> CategoryMap:
        return self._category_map

    def profile_for_category(self, category: str) -> str:
        """Profile name bound to ``category`` (unknown -> default, restricted)."""
        return self._category_map.profile_for(category)

    def resolve_profile(self, request: ExecutionRequest) -> SecurityProfile:
        """Resolve the profile for a request (fail closed, never widened)."""
        if request.profile_name is not None:
            # An explicit override that does not exist resolves to the most
            # secure profile - asking for a weaker/nonexistent profile never
            # widens the sandbox.
            return resolve_profile(request.profile_name, self._catalog)
        return resolve_profile(
            self._category_map.profile_for(request.category), self._catalog
        )

    # ------------------------------------------------------------------ #
    # execution
    # ------------------------------------------------------------------ #
    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        """Route an execution through the sandbox and return its result.

        Raises :class:`SandboxDisabledError` when the executor or its runtime
        is disabled (fail closed - a disabled sandbox never degrades into an
        unsandboxed run), and returns a denied :class:`ExecutionResult` when
        the resolved profile refuses the request.
        """
        if not self.enabled:
            raise SandboxDisabledError(
                f"sandbox executor is not enabled; refusing to execute "
                f"{request.tool!r}"
            )
        if not isinstance(request, ExecutionRequest):
            raise SandboxConfigError(
                "execute() requires an ExecutionRequest, got "
                f"{type(request).__name__}"
            )

        profile = self.resolve_profile(request)

        if not self._runtime.enabled:
            raise RuntimeNotEnabledError(
                f"runtime {self._runtime.name!r} is flag-gated OFF; refusing "
                f"to execute {request.tool!r} (a disabled sandbox never "
                f"degrades into an unsandboxed run)"
            )

        result = self._runtime.run(request, profile)
        # Canonical identity on the result (category/tool/profile always name
        # the request as dispatched, regardless of the runtime).
        result = replace(
            result,
            profile=profile.name,
            category=request.category,
            tool=request.tool,
        )
        self._audit_execution(request, profile, result)
        return result

    # ------------------------------------------------------------------ #
    # audit (optional; mcp-shaped append-only sink)
    # ------------------------------------------------------------------ #
    def _audit_execution(
        self,
        request: ExecutionRequest,
        profile: SecurityProfile,
        result: ExecutionResult,
    ) -> None:
        if self._audit is None:
            return
        detail = {
            "tool": request.tool,
            "category": request.category,
            "profile": profile.name,
            "operation": request.operation,
            "outcome": "accepted" if result.accepted else "sandbox_denied",
            "reason": result.reason,
            "sandbox": dict(result.output),
        }
        self._audit.append(
            "tool_call",
            status="ok" if result.accepted else "error",
            tenant_id=request.tenant_id,
            agent_id=request.agent_id,
            actor=request.actor,
            detail=detail,
        )
