"""The read-only enterprise tool family for grounded chat (issue #504).

EPIC #500 adds an **enterprise** family to the declared MCP catalogue: the ten
read-only tools a grounded chat turn reaches its facts through. Two of the ten
(``kb.query`` / ``kb.freshness``) are already declared by issue #20 and are
**left exactly as they are** - this module declares the eight new ones and names
the reused pair, so the family's membership is stated once and can be gated.

Read-only, by construction (ADR-0023):

* every tool is a *read* of an authority declared in :mod:`mcp.sources`; no
  tool here has a write path, and :data:`WRITE_TOOLS` is empty on purpose so a
  test can hold the family to that;
* an action a turn wants is an **approval proposal**
  (:class:`mcp.grounding.ApprovalProposal`) for the identity lane to route.

**A tool argument can never select a tenant.** The argument vocabulary below
does not contain a tenant key at all, and the tenant a tenant-scoped read runs
under is taken from the *verified session* - so a caller cannot widen its scope
by asking. A caller that supplies ``tenant`` / ``tenant_id`` / ``tenantId`` is
refused by name rather than silently ignored: an ignored scope selector teaches
a caller the wrong lesson, and a refusal is auditable.

---knowledge---
module_id: gateway.mcp.enterprise
system: gateway
app: mcp
solution_class: enterprise
patterns: [read-only-by-construction, declared-family, refuse-tenant-argument]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [enterprise_schemas, enterprise_descriptions, enterprise_handlers, WRITE_TOOLS, TenantArgumentRefused, UnknownArgumentRefused]
invariants: "no tool in this family has a write path, and WRITE_TOOLS is empty on purpose so a test can hold the family to that"
gotchas: "an action a turn wants is an approval proposal, never a write; a tenant may not be selected through a tool argument"
related: ["#504", "#500"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Tuple

from .errors import InvalidArgumentsError
from .model import SessionIdentity
from .sources import (
    MODE_PRODUCTION,
    TENANT_ARGUMENT_KEYS,
    TENANT_SCOPED_FAMILIES,
    SourceCatalog,
    default_repo_root,
)

# ``TENANT_ARGUMENT_KEYS`` / ``TENANT_SCOPED_FAMILIES`` are the *authority*
# layer's vocabulary (``mcp.sources``), consumed here and re-exported so the
# tool family and the grounding assembler cannot drift apart on which families
# are tenant-scoped or which argument names are refused.
__all__ = [
    "CHAT_FAMILY_NAMES",
    "DESCRIPTIONS",
    "ENTERPRISE_TOOL_NAMES",
    "REUSED_READ_TOOLS",
    "TENANT_ARGUMENT_KEYS",
    "TENANT_SCOPED_FAMILIES",
    "WRITE_TOOLS",
    "enterprise_descriptions",
    "enterprise_handlers",
    "enterprise_schemas",
]

Handler = Callable[[Dict[str, Any], SessionIdentity, Optional[Any]], Dict[str, Any]]

#: The eight tools this issue registers (the existing seven stay untouched).
ENTERPRISE_TOOL_NAMES: Tuple[str, ...] = (
    "agent.list",
    "agent.status",
    "budget.status",
    "fleet.snapshot",
    "ledger.tail",
    "ledger.verify",
    "ticket.get",
    "ticket.search",
)

#: Declared by issue #20 and *consumed* here rather than re-declared. Both read
#: the declared-fake in-memory graph (``gateway/mcp/kb.py``), which ADR-0018 §2
#: confines to offline/test/demo use - which is why the grounding assembler
#: refuses them on a production path (``mcp.sources.KbFixtureSource``).
REUSED_READ_TOOLS: Tuple[str, ...] = ("kb.freshness", "kb.query")

#: The whole read-only family (eight new + two reused): the ten of issue #504.
CHAT_FAMILY_NAMES: Tuple[str, ...] = ENTERPRISE_TOOL_NAMES + REUSED_READ_TOOLS

#: No tool in this family writes. Empty on purpose - a gate and a test hold it.
WRITE_TOOLS: Tuple[str, ...] = ()

#: tool -> (authority family, read operation).
_TOOL_SPECS: Dict[str, Tuple[str, str]] = {
    "ticket.get": ("board", "ticket"),
    "ticket.search": ("board", "search"),
    "budget.status": ("budget", "status"),
    "ledger.tail": ("ledger", "tail"),
    "ledger.verify": ("ledger", "verify"),
    "agent.list": ("registry", "list"),
    "agent.status": ("registry", "status"),
    "fleet.snapshot": ("fleet", "snapshot"),
}

#: tool -> declared arguments: caller name -> (read keyword, coercion).
_ARGUMENTS: Dict[str, Dict[str, Tuple[str, str]]] = {
    "ticket.get": {"number": ("number", "int")},
    "ticket.search": {
        "text": ("text", "str"),
        "state": ("state", "str"),
        "label": ("label", "str"),
        "limit": ("limit", "window"),
    },
    "budget.status": {},
    "ledger.tail": {"limit": ("limit", "window")},
    "ledger.verify": {},
    "agent.list": {"limit": ("limit", "window")},
    "agent.status": {"agent_id": ("agent_id", "str")},
    "fleet.snapshot": {},
}

#: tool -> the arguments a caller must supply (the rest are optional filters).
_REQUIRED_ARGUMENTS: Dict[str, Tuple[str, ...]] = {
    "ticket.get": ("number",),
    "agent.status": ("agent_id",),
}

_SCHEMA_DESCRIPTIONS: Dict[str, str] = {
    "number": "Board issue number (the ticket id).",
    "text": "Substring of the ticket title.",
    "state": "Ticket state, e.g. OPEN / CLOSED.",
    "label": "Require this label.",
    "limit": "Maximum fragments to return (1-200, default 50).",
    "agent_id": "Agent profile id as declared in registry/profiles.",
}

DESCRIPTIONS: Dict[str, str] = {
    "ticket.get": (
        "Read one ticket (the single join node, ADR-0014) from the board "
        "snapshot. Read-only: status, blocked_by and facets are never written."
    ),
    "ticket.search": "Search the board snapshot for tickets by title text, state or label.",
    "budget.status": (
        "Read this tenant's DECLARED budget policy (labelled declared-policy; "
        "measured spend lives in the metering feed and is not re-derived here)."
    ),
    "ledger.tail": "Read the tail of this tenant's hash-chained audit ledger.",
    "ledger.verify": (
        "Verify this tenant's ledger chain end-to-end; carries the ledger's own "
        "tri-state verdict (OK / NOT-OK / CANNOT-ASSESS) verbatim."
    ),
    "agent.list": "List the agent profiles the platform registry serves.",
    "agent.status": "Read one agent profile by id from the platform registry.",
    "fleet.snapshot": (
        "Read the ao.bridge/v1 manifest: one row per state family with that "
        "family's own revision (a change signal, not a second projection)."
    ),
}

#: The declared read argument bounds (no caller-supplied tenant, ever).
_MAX_WINDOW = 200


class TenantArgumentRefused(InvalidArgumentsError):
    """A caller tried to select a tenant through a tool argument."""


class UnknownArgumentRefused(InvalidArgumentsError):
    """A caller supplied an argument the tool does not declare."""


def _coerce(name: str, kind: str, value: Any) -> Any:
    if kind == "int":
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise InvalidArgumentsError(
                f"argument {name!r} must be an integer, got {value!r}"
            ) from exc
    if kind == "window":
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise InvalidArgumentsError(
                f"argument {name!r} must be an integer, got {value!r}"
            ) from exc
        if number < 1 or number > _MAX_WINDOW:
            raise InvalidArgumentsError(
                f"argument {name!r} must be within 1..{_MAX_WINDOW}, got {number}"
            )
        return number
    if not isinstance(value, str) or not value.strip():
        raise InvalidArgumentsError(
            f"argument {name!r} must be a non-empty string, got {value!r}"
        )
    return value


def _validate_arguments(
    tool: str, arguments: Dict[str, Any], declared: Dict[str, Tuple[str, str]]
) -> Dict[str, Any]:
    """Coerce the declared arguments; refuse a tenant selector by name."""
    for key in arguments:
        if key in TENANT_ARGUMENT_KEYS:
            raise TenantArgumentRefused(
                f"tool {tool!r} refuses a caller-supplied tenant selector "
                f"({key!r}): every read runs under the verified session's tenant "
                f"and no argument may widen it (ADR-0023)"
            )
    unknown = sorted(set(arguments) - set(declared))
    if unknown:
        raise UnknownArgumentRefused(
            f"tool {tool!r} does not declare the argument(s) {', '.join(unknown)} "
            f"(declared: {', '.join(sorted(declared)) or 'none'})"
        )
    return {
        keyword: _coerce(name, kind, arguments[name])
        for name, (keyword, kind) in declared.items()
        if name in arguments
    }


def _envelope(result: Any, *, tool: str, tenant_id: str) -> Dict[str, Any]:
    """The uniform read envelope: status, citations, fragments - or NO_DATA."""
    return {
        "tool": tool,
        "tenantId": tenant_id,
        "status": result.status,
        "count": len(result.fragments),
        "reason": result.reason,
        "citations": list(result.citations()),
        "fragments": [fragment.as_dict() for fragment in result.fragments],
    }


def enterprise_schemas() -> Dict[str, Dict[str, object]]:
    """The declared JSON schema of every tool in the family."""
    schemas: Dict[str, Dict[str, object]] = {}
    for tool, declared in _ARGUMENTS.items():
        properties = {
            name: {
                "type": "integer" if kind in ("int", "window") else "string",
                "description": _SCHEMA_DESCRIPTIONS.get(name, ""),
            }
            for name, (_keyword, kind) in declared.items()
        }
        schemas[tool] = {
            "type": "object",
            "properties": properties,
            "required": list(_REQUIRED_ARGUMENTS.get(tool, ())),
            "additionalProperties": False,
        }
    return schemas


def enterprise_descriptions() -> Dict[str, str]:
    """The declared description of every tool in the family."""
    return dict(DESCRIPTIONS)


def enterprise_handlers(
    catalog: Optional[SourceCatalog] = None,
) -> Dict[str, Handler]:
    """The family's handlers, each closing over one read-only source catalogue.

    The catalogue is the only way a handler can reach a fact, and it is the
    catalogue - not the caller - that decides whether a family may be read at
    all (a ``fixture_only`` family is refused there on a production path).
    """
    sources = catalog if catalog is not None else SourceCatalog.from_repo_root(
        default_repo_root(), mode=MODE_PRODUCTION
    )

    def build(tool: str) -> Handler:
        family, operation = _TOOL_SPECS[tool]
        declared = _ARGUMENTS[tool]
        tenant_scoped = family in TENANT_SCOPED_FAMILIES

        def handler(
            arguments: Dict[str, Any],
            session: SessionIdentity,
            backend: Optional[Any] = None,
        ) -> Dict[str, Any]:
            kwargs = _validate_arguments(tool, arguments or {}, declared)
            if tenant_scoped:
                # The tenant comes from the verified session, never from args.
                kwargs["tenant_id"] = session.tenant_id
            result = sources.call(family, operation, **kwargs)
            return _envelope(result, tool=tool, tenant_id=session.tenant_id)

        handler.__name__ = tool.replace(".", "_")
        handler.__doc__ = DESCRIPTIONS[tool]
        return handler

    return {tool: build(tool) for tool in ENTERPRISE_TOOL_NAMES}
