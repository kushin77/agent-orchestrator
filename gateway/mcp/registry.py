"""Tool registry: declared capabilities with schemas (fail closed).

A tool is a declared capability with a JSON schema (the acceptance-criteria
shape). The registry only ever answers for tools it declares: an unknown name
raises :class:`UnknownToolError` (mapped to JSON-RPC ``-32601``) instead of
falling through - the saas-rbac allowlist doctrine (allowlist, not denylist).
Schemas enumerate in sorted name order so ``tools/list`` output is
deterministic (the canonical-ordering property of the codeidx MCP server).

---knowledge---
module_id: gateway.mcp.registry
system: gateway
app: mcp
solution_class: enterprise
patterns: [allowlist-not-denylist, fail-closed, deterministic-ordering]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [ToolRegistry]
invariants: "the registry answers only for tools it declares: an unknown name raises instead of falling through"
gotchas: "schemas enumerate in sorted name order so tools/list output is deterministic"
related: ["#20"]
do_not_duplicate: null
---knowledge---
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

    def extend(self, other: "ToolRegistry") -> "ToolRegistry":
        """Compose another registry's declarations into this one.

        Used to place a *family* of tools beside the base catalogue (issue
        #504) without rewriting it: every declaration of ``other`` is registered
        here, and the duplicate guard still refuses a name declared twice - so
        a family can never silently shadow a tool it does not own.
        """
        for name in other.names():
            self.register(other.require(name))
        return self

    def names(self) -> List[str]:
        return sorted(self._tools)

    def schemas(self) -> List[Dict[str, object]]:
        """Declared schemas in sorted name order (deterministic tools/list)."""
        return [self._tools[name].schema() for name in self.names()]

    def __len__(self) -> int:
        return len(self._tools)
