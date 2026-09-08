"""Tool registry: declared capabilities with schemas (fail closed).

A tool is a declared capability with a JSON schema (the acceptance-criteria
shape). The registry only ever answers for tools it declares: an unknown name
raises :class:`UnknownToolError` (mapped to JSON-RPC ``-32601``) instead of
falling through - the saas-rbac allowlist doctrine (allowlist, not denylist).
Schemas enumerate in sorted name order so ``tools/list`` output is
deterministic (the canonical-ordering property of the codeidx MCP server).
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .errors import UnknownToolError
from .model import ToolDefinition


class ToolRegistry:
    """The gateway's declared, closed tool set."""

    def __init__(self) -> None:
        self._tools: Dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> "ToolRegistry":
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool registration: {tool.name!r}")
        self._tools[tool.name] = tool
        return self

    def get(self, name: str) -> Optional[ToolDefinition]:
        return self._tools.get(name)

    def require(self, name: str) -> ToolDefinition:
        tool = self._tools.get(name)
        if tool is None:
            raise UnknownToolError(f"unknown tool {name!r} (not a declared capability)")
        return tool

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> List[str]:
        return sorted(self._tools)

    def schemas(self) -> List[Dict[str, object]]:
        """Declared schemas in sorted name order (deterministic tools/list)."""
        return [self._tools[name].schema() for name in self.names()]

    def __len__(self) -> int:
        return len(self._tools)
