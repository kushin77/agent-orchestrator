"""Additive seam wiring the sandbox into the MCP tool gateway (issue #20).

The MCP tool gateway (``gateway/mcp``, merged issue #20) is a read-only
surface for this lane. Sandboxing is wired STRICTLY ADDITIVELY: a sandbox-
routed tool is a normal declared capability registered into the gateway's
``ToolRegistry`` whose handler routes the execution through a
:class:`SandboxExecutor` before acting. A denial raises
:class:`SandboxDeniedError`, which the gateway's normal error path converts
into an ``isError`` result and an audit record (status ``error``); an accepted
execution returns the sandbox's result payload and is audited status ``ok``.
The gateway's append-only ledger is untouched, and the sandbox's own optional
audit sink adds an execution-level record.

The gateway/mcp code is imported read-only (lazily, so importing this package
never requires ``gateway/`` on ``sys.path``) and is never modified. The full
seam is proven end-to-end by ``tests/test_mcp_integration.py``.
"""

from __future__ import annotations

from typing import Any, Callable, Dict

from .errors import SandboxConfigError, SandboxDeniedError
from .executor import SandboxExecutor
from .model import ExecutionRequest

# builder(args, session, backend, tool_name, category) -> ExecutionRequest
RequestBuilder = Callable[[Dict[str, Any], Any, Any, str, str], ExecutionRequest]


def _mcp_model():
    """Import the merged gateway/mcp model read-only (gateway/ on sys.path)."""
    from mcp import model  # read-only import; never modified here

    return model


def tool_call_permission() -> str:
    """The gateway's ``tool:call`` permission id (consumed, not redefined)."""
    return _mcp_model().TOOL_CALL_PERMISSION


def sandboxed_tool(
    *,
    executor: SandboxExecutor,
    name: str,
    description: str,
    input_schema: Dict[str, Any],
    category: str,
    request_builder: RequestBuilder,
    allowlist_id: str = "",
    permission: str = "",
):
    """Build an mcp ``ToolDefinition`` whose handler routes through the sandbox.

    The returned definition is registered into a gateway ``ToolRegistry`` like
    any other declared capability. On each call the handler builds an
    :class:`ExecutionRequest` from the arguments, attaches the session identity
    for audit, and dispatches it to ``executor``. A denial raises
    :class:`SandboxDeniedError` (surfaced + audited by the gateway's normal
    error path); an accepted execution returns the sandbox result payload.
    """
    model = _mcp_model()
    perm = permission or model.TOOL_CALL_PERMISSION

    def _handler(args: Dict[str, Any], session: Any, backend: Any) -> Dict[str, Any]:
        request = request_builder(args, session, backend, name, category)
        if not isinstance(request, ExecutionRequest):
            raise SandboxConfigError(
                f"request_builder for tool {name!r} must return an "
                f"ExecutionRequest, got {type(request).__name__}"
            )
        session_id = getattr(session, "agent_id", None) or ""
        request = request.with_identity(
            tenant_id=getattr(session, "tenant_id", None),
            agent_id=getattr(session, "agent_id", None),
            actor=getattr(session, "subject", None) or session_id,
        )
        result = executor.execute(request)
        if result.denied:
            raise SandboxDeniedError(
                f"tool {name!r} ({category}) denied by sandbox: {result.reason}"
            )
        return {
            "tool": name,
            "category": category,
            "profile": result.profile,
            "runtime": result.runtime,
            "sandbox": dict(result.output),
        }

    return model.ToolDefinition(
        name=name,
        description=description,
        input_schema=input_schema,
        handler=_handler,
        allowlist_id=allowlist_id or name,
        permission=perm,
    )


def category_of(tool_name: str, mapping: Dict[str, str], default: str = "file") -> str:
    """Resolve a tool's category from a deployment-supplied name map.

    The category map is an explicit allowlist-style publication decision (a
    tool without a declared category is treated as the most restrictive one by
    the executor, which fails unknown categories closed to restricted). This
    helper only labels known tools; it never invents a category.
    """
    return mapping.get(tool_name, default)
