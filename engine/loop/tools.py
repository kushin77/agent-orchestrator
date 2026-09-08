"""engine/loop.tools — allowlisted tool execution.

Issue #23 acceptance #1: *each step tool-call is validated against the
profile tool allowlist* before it runs.  :class:`ToolRegistry` is the gate —
it holds the allowlist and the injected :class:`ToolExecutor`, admits only
allowlisted tools, and returns a typed :class:`ToolResult` for everything
(a rejected call is a *failed* result with an explicit reason, never a
silent no-op).  Executors are injected and deterministic for replay: the
same tool call must return the same result, which is what makes two loops
over the same inputs produce the same trace.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence

from .model import ToolCall, ToolResult
from .schema import validate_output


class ToolExecutor(Protocol):
    """What a tool executor must provide (duck-typed, offline-injectable)."""

    def execute(self, name: str, arguments: Mapping[str, Any]) -> Any:
        """Run tool ``name`` with ``arguments``; return a JSON-safe value.

        Raises ``ToolExecutionError`` (or any exception) on failure — the
        registry turns any exception into a failed :class:`ToolResult`.
        """
        ...


class ToolExecutionError(Exception):
    """Raised by an executor when a tool call fails at runtime."""


@dataclass(frozen=True)
class ToolSpec:
    """Declaration of one executable tool (name + optional argument schema)."""

    name: str
    arg_schema: Optional[Mapping[str, Any]] = None  # declarative schema


class ToolRegistry:
    """The allowlist gate between the loop and tool execution.

    ``executor`` performs the actual work; ``allowlist`` is the set of tool
    names the profile admits; ``specs`` optionally declare per-tool argument
    schemas (validated against the tool-call arguments before execution).
    """

    def __init__(
        self,
        executor: Any,
        allowlist: Sequence[str] = (),
        specs: Optional[Mapping[str, ToolSpec]] = None,
    ) -> None:
        self.executor = executor
        self._allow = set(allowlist or ())
        self.specs = specs or {}

    def allows(self, tool: str) -> bool:
        """Whether ``tool`` is allowlisted (empty allowlist admits all)."""
        if not self._allow:
            return True
        return tool in self._allow

    def validate_arguments(self, tool: str, arguments: Mapping[str, Any]) -> Optional[str]:
        """Validate tool-call arguments against a declared arg schema."""
        spec = self.specs.get(tool)
        if spec is None or spec.arg_schema is None:
            return None
        _, error = validate_output(arguments, spec.arg_schema)
        return error

    def run(self, call: ToolCall) -> ToolResult:
        """Run one tool call through the allowlist gate.

        A non-allowlisted tool never reaches the executor: it returns a
        failed :class:`ToolResult` whose error names the violation, so the
        loop's allowlist policy (``retry`` or ``cannot_assess``) can act on
        it deterministically.
        """
        if not self.allows(call.name):
            return ToolResult(
                name=call.name,
                ok=False,
                error=f"tool {call.name!r} is not in the profile tool allowlist",
            )
        arg_error = self.validate_arguments(call.name, call.arguments)
        if arg_error is not None:
            return ToolResult(
                name=call.name,
                ok=False,
                error=f"tool {call.name!r} arguments failed validation: {arg_error}",
            )
        try:
            content = self.executor.execute(call.name, dict(call.arguments))
        except Exception as exc:  # noqa: BLE001 - any tool death is a failed call
            return ToolResult(name=call.name, ok=False, error=f"{type(exc).__name__}: {exc}")
        return ToolResult(name=call.name, ok=True, content=content)


class FunctionExecutor:
    """Adapter that turns a plain ``fn(name, arguments) -> Any`` into an executor."""

    def __init__(self, fn: Callable[[str, Mapping[str, Any]], Any]) -> None:
        self._fn = fn

    def execute(self, name: str, arguments: Mapping[str, Any]) -> Any:
        return self._fn(name, arguments)
