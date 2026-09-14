"""The OpenAPI components, EMITTED from the frozen contracts and the adapter (#413).

Two sources, both read — never restated:

* the **three frozen seam schemas** (``docs/contracts/paperclip/{ticket,
  heartbeat,budget}.schema.json``) are embedded verbatim (minus ``$schema`` /
  ``$id``) as ``Ticket`` / ``Heartbeat`` / ``Budget``. The ticket contract's v2
  ``facets`` / ``authority`` fields (#400) ride along because they are in the
  schema, not because this module knows about them;
* the **adapter's own resource shapes** are introspected from
  ``integrations/paperclip/model.py`` — a sample instance of each is built and
  ``to_dict()`` is called, and the JSON shape is derived from the returned value
  and the dataclass type hints. If a shape were not introspectable this module
  would say so (``x-shape-provenance`` records the source either way) rather
  than inventing a second description.

The one shape that is not derivable from either source — the error envelope —
is declared exactly once, in :func:`error_schema`, and referenced by every
taxonomy response.
"""

from __future__ import annotations

import typing
from pathlib import Path
from typing import Any, Dict, Tuple

from .. import mapping as _mapping
from ..model import Activity, Agent, Approval

#: The three frozen seam contracts, and the component name each is emitted as.
CONTRACT_COMPONENTS: Tuple[Tuple[str, str], ...] = (
    ("heartbeat", "Heartbeat"),
    ("ticket", "Ticket"),
    ("budget", "Budget"),
)

#: The adapter resource shapes to introspect: component name -> model type.
ADAPTER_COMPONENTS: Tuple[Tuple[str, Any], ...] = (
    ("Agent", Agent),
    ("Approval", Approval),
    ("Activity", Activity),
)

#: Sample builders — an all-defaults instance per adapter type. The *keys* of
#: ``to_dict()`` are the shape; only the values are throwaway.
_SAMPLES: Dict[str, Any] = {
    "Agent": lambda: Agent(agent_id="", name="", role="", reports_to="", hired=False, tier=""),
    "Approval": lambda: Approval(id="", kind="", scope_level="", scope_id="", amount=0.0, state=""),
    "Activity": lambda: Activity(id="", actor="", verb="", object_ref="", ts=""),
}

_PRIMITIVES: Dict[Any, Dict[str, Any]] = {
    str: {"type": "string"},
    bool: {"type": "boolean"},
    int: {"type": "integer"},
    float: {"type": "number"},
    dict: {"type": "object"},
}


def contract_components(root: Path) -> Dict[str, Dict[str, Any]]:
    """The three frozen schemas as OpenAPI components, keyed by component name.

    ``docs/contracts/paperclip/<kind>.schema.json`` is the source; only the
    document-identity keywords (``$schema`` / ``$id``) are dropped, because the
    component lives inside the OpenAPI document, not at its own URL.
    """
    schemas = _mapping.load_schemas(Path(root))
    out: Dict[str, Dict[str, Any]] = {}
    for kind, component in CONTRACT_COMPONENTS:
        schema = schemas[kind]
        out[component] = {k: v for k, v in schema.items() if k not in ("$schema", "$id")}
    return out


def contract_sources() -> Dict[str, str]:
    """Component name -> the contract file it is emitted from (for reporting)."""
    return {
        component: _mapping.SCHEMA_DIR.joinpath(f"{kind}.schema.json").as_posix()
        for kind, component in CONTRACT_COMPONENTS
    }


def _hint_type(hint: Any) -> Dict[str, Any]:
    """A JSON-schema fragment for a dataclass field's type hint (best effort)."""
    if hint is None:
        return {}
    origin = typing.get_origin(hint)
    args = typing.get_args(hint)
    if origin in (tuple, list):
        item = args[0] if args and args[0] is not Ellipsis else None
        return {"type": "array", "items": _hint_type(item) if item is not None else {}}
    if origin is dict:
        return {"type": "object"}
    if origin is typing.Union:
        named = [a for a in args if a is not type(None)]
        return _hint_type(named[0]) if len(named) == 1 else {}
    return dict(_PRIMITIVES.get(hint, {}))


def _derive(value: Any, hint: Any = None) -> Dict[str, Any]:
    """Derive a JSON-schema fragment from a value, using the hint for empties.

    The value decides (it is what ``to_dict`` really emits); a type hint only
    fills a gap the value cannot — an empty array whose element type is declared.
    """
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, dict):
        keys = sorted(value)
        return {
            "type": "object",
            "properties": {key: _derive(value[key]) for key in keys},
            "required": keys,
            "additionalProperties": False,
        }
    if isinstance(value, (list, tuple)):
        if value:
            items = _derive(value[0])
        else:
            items = _hint_type(hint) or {}
            if items.get("type") == "array":
                items = items.get("items") or {}
        return {"type": "array", "items": items}
    if value is None:
        return {"type": "null"}
    return {}


def derive_schema(value: Any, hint: Any = None) -> Dict[str, Any]:
    """Public wrapper over the sample-shape derivation (used by the health schema)."""
    return _derive(value, hint)


def adapter_components() -> Dict[str, Dict[str, Any]]:
    """The adapter's own resource shapes, introspected from ``to_dict()``.

    For each type a sample instance is built, ``to_dict()`` is called, and the
    emitted mapping's shape is derived — so the document cannot describe a field
    the adapter does not emit, nor miss one it does. ``x-shape-provenance``
    records the exact source.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for component, cls in ADAPTER_COMPONENTS:
        sample = _SAMPLES[component]().to_dict()
        hints = typing.get_type_hints(cls)
        properties: Dict[str, Any] = {}
        for key in sorted(sample):
            properties[key] = _derive(sample[key], hints.get(key))
        out[component] = {
            "title": component,
            "description": (
                f"Introspected from integrations/paperclip/model.py::{component}.to_dict — "
                "the exact shape the adapter emits, never a parallel description."
            ),
            "type": "object",
            "properties": properties,
            "required": sorted(sample),
            "additionalProperties": False,
            "x-shape-provenance": f"integrations/paperclip/model.py::{component}.to_dict",
        }
    return out


def error_schema() -> Dict[str, Any]:
    """The boundary error envelope every taxonomy status carries."""
    return {
        "title": "Error",
        "description": (
            "The mapped boundary error envelope. ``status`` is one of the seven taxonomy "
            "statuses; ``code`` is the fleet's own stable machine code "
            "(identity/cpapi/errors.py vocabulary). No field ever carries a credential."
        ),
        "type": "object",
        "additionalProperties": False,
        "required": ["status", "code", "message"],
        "properties": {
            "status": {
                "type": "integer",
                "description": "the HTTP status (one of 400/401/403/404/409/422/503)",
            },
            "code": {
                "type": "string",
                "description": "the stable machine wire code for this refusal",
            },
            "message": {"type": "string", "description": "a human-readable refusal"},
            "details": {
                "type": "object",
                "description": "optional structured detail; never a credential (GR-6)",
            },
        },
    }
