"""Declared tool catalog of the MCP surface.

Every tool is a declared capability with a JSON schema. Two families:

- **Indexing tools** (tenant code/KB access) - re-use the compiler-accurate
  shapes from the code-indexing MCP catalog (``codeidx`` definitions /
  references / search) and the CMR indexer (``index.query`` / freshness).
  Handlers receive the per-tenant :class:`kb.MemoryKbBackend` bound to the
  caller's tenant, so results can never cross a tenant boundary.
- **Platform tools** - a ``platform.whoami`` informational tool that proves
  every call carries a resolved tenant context.

Handler contract: ``handler(args, session, backend) -> dict`` where ``backend``
is the caller-tenant index backend (``None`` for platform tools that do not
touch tenant data). Handlers return data; the gateway wraps exceptions into an
``isError`` text result (failures are data, not protocol errors - codeidx).
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from .kb import IndexBackend
from .model import SessionIdentity, ToolDefinition
from .registry import ToolRegistry

Handler = Callable[[Dict[str, Any], SessionIdentity, Optional[IndexBackend]], Dict[str, Any]]


def _required_schema(props: Dict[str, object], required: list) -> Dict[str, object]:
    return {"type": "object", "properties": props, "required": required}


def _code_definitions(args: Dict[str, Any], session: SessionIdentity,
                      backend: Optional[IndexBackend]) -> Dict[str, Any]:
    return backend.definitions(
        args["symbol"], repo=args.get("repo"), limit=int(args.get("limit", 100))
    )


def _code_references(args: Dict[str, Any], session: SessionIdentity,
                     backend: Optional[IndexBackend]) -> Dict[str, Any]:
    return backend.references(
        args["symbol"], repo=args.get("repo"), limit=int(args.get("limit", 500))
    )


def _code_search(args: Dict[str, Any], session: SessionIdentity,
                 backend: Optional[IndexBackend]) -> Dict[str, Any]:
    return backend.search(
        args["q"], repo=args.get("repo"), limit=int(args.get("limit", 100))
    )


def _kb_query(args: Dict[str, Any], session: SessionIdentity,
              backend: Optional[IndexBackend]) -> Dict[str, Any]:
    return backend.query(module_id=args.get("module_id"), repo=args.get("repo"))


def _kb_freshness(args: Dict[str, Any], session: SessionIdentity,
                  backend: Optional[IndexBackend]) -> Dict[str, Any]:
    return backend.freshness(args["repo"])


def _kb_summary(args: Dict[str, Any], session: SessionIdentity,
                backend: Optional[IndexBackend]) -> Dict[str, Any]:
    return backend.summary()


def _platform_whoami(args: Dict[str, Any], session: SessionIdentity,
                     backend: Optional[IndexBackend]) -> Dict[str, Any]:
    return {
        "tenantId": session.tenant_id,
        "agentId": session.agent_id,
        "subject": session.subject,
        "role": session.role,
    }


def declared_tools() -> Dict[str, Handler]:
    """The declared tool catalog (name -> handler)."""
    return {
        "code.definitions": _code_definitions,
        "code.references": _code_references,
        "code.search": _code_search,
        "kb.query": _kb_query,
        "kb.freshness": _kb_freshness,
        "kb.summary": _kb_summary,
        "platform.whoami": _platform_whoami,
    }


def build_registry() -> ToolRegistry:
    """Build a :class:`ToolRegistry` with every declared tool + schema."""
    registry = ToolRegistry()
    handlers = declared_tools()

    schemas = {
        "code.definitions": _required_schema(
            {
                "symbol": {"type": "string", "description": "Exact symbol name."},
                "repo": {"type": "string", "description": "Optional tenant repo to scope to."},
                "limit": {"type": "integer", "description": "Max hits (default 100)."},
            },
            ["symbol"],
        ),
        "code.references": _required_schema(
            {
                "symbol": {"type": "string", "description": "Exact symbol name."},
                "repo": {"type": "string", "description": "Optional tenant repo to scope to."},
                "limit": {"type": "integer", "description": "Max hits (default 500)."},
            },
            ["symbol"],
        ),
        "code.search": _required_schema(
            {
                "q": {"type": "string", "description": "Substring of a symbol name."},
                "repo": {"type": "string", "description": "Optional tenant repo to scope to."},
                "limit": {"type": "integer", "description": "Max hits (default 100)."},
            },
            ["q"],
        ),
        "kb.query": {
            "type": "object",
            "properties": {
                "module_id": {"type": "string", "description": "Optional module id."},
                "repo": {"type": "string", "description": "Optional tenant repo."},
            },
        },
        "kb.freshness": _required_schema(
            {
                "repo": {"type": "string", "description": "Tenant repo name."},
            },
            ["repo"],
        ),
        "kb.summary": {"type": "object", "properties": {}},
        "platform.whoami": {"type": "object", "properties": {}},
    }

    descriptions = {
        "code.definitions": "Find where a symbol is defined in this tenant's code index. Exact, index-derived.",
        "code.references": "Find resolved references (call sites) of a symbol in this tenant's code index.",
        "code.search": "Search this tenant's symbol definitions by substring of the symbol name.",
        "kb.query": "Query this tenant's code/KB graph: one repo, one module, or the full tenant graph.",
        "kb.freshness": "Read-only freshness facts for one repo of this tenant's KB (unknown is never up-to-date).",
        "kb.summary": "Counts + shape of this tenant's KB graph snapshot.",
        "platform.whoami": "Return the resolved tenant context of the current session (identity echo).",
    }

    for name, handler in handlers.items():
        registry.register(
            ToolDefinition(
                name=name,
                description=descriptions[name],
                input_schema=schemas[name],
                handler=handler,
            )
        )
    return registry
