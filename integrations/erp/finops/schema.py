"""A stdlib-only JSON-Schema subset validator, and the keyword freeze (#654).

This lane ships four JSON declarations — the rate card, the budget policy, the
harvest record and the metered-event shape — and they are validated by a
**stdlib-only JSON-Schema subset**, following this repository's own convention
(``integrations/erp/core/schema.py``, ``integrations/erp/crm/schema.py``,
``governance/modules/schema.py``, ``guardrails/policy``). The repository keeps a
per-lane validator rather than one shared engine for a reason that shows up
here: each lane freezes the keyword set *it* enforces, so no lane can quietly
depend on a keyword another lane later drops.

Three properties make the freeze worth having:

* **an ignored keyword is refused.** :func:`assert_supported` rejects a schema
  that uses a keyword this validator does not implement. A schema is a promise;
  a promise nothing enforces is worse than no schema, because the artifact
  looks constrained and is not.
* **the violation names the row.** :func:`validate` reports
  ``<path>: <what is wrong>`` in document order, so a refusal names the field
  instead of "the card is invalid".
* **no silent coercion.** ``True`` is not an integer and ``1`` is not ``"1"``:
  ``bool`` is a subclass of ``int`` in Python, and that is the classic way a
  type check quietly stops measuring.

---knowledge---
module_id: integrations.erp.finops.schema
system: integrations
app: erp
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [check_schema, assert_supported, load, load_and_refuse, validate]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping

from .model import Refused

__all__ = [
    "DIALECT",
    "KEYWORDS",
    "assert_supported",
    "check_schema",
    "load",
    "load_and_refuse",
    "validate",
]

#: Every keyword this validator implements. A schema using anything else is
#: refused by name rather than silently under-enforced.
KEYWORDS = frozenset(
    {
        "$comment",
        "$id",
        "$schema",
        "additionalProperties",
        "const",
        "description",
        "enum",
        "items",
        "maxLength",
        "maximum",
        "minLength",
        "minimum",
        "pattern",
        "properties",
        "required",
        "title",
        "type",
    }
)

DIALECT = "https://json-schema.org/draft/2020-12/schema"

_TYPE_MAP: Dict[str, type] = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
}


def check_schema(schema: Any, where: str = "schema") -> List[str]:
    """Every keyword in ``schema`` this validator does not implement.

    Walks nested schemas as well as the root, so a keyword hidden under
    ``properties`` is found: a freeze that only inspects the top level is how
    an unenforceable promise survives review.
    """
    problems: List[str] = []
    if isinstance(schema, dict):
        for keyword in sorted(schema):
            if keyword not in KEYWORDS:
                problems.append(
                    f"{where}: unsupported keyword {keyword!r} is not enforced "
                    f"(supported: {', '.join(sorted(KEYWORDS))})"
                )
        for keyword, subschema in (schema.get("properties") or {}).items():
            problems.extend(
                check_schema(subschema, where=f"{where}.properties.{keyword}")
            )
        items = schema.get("items")
        if items is not None:
            problems.extend(check_schema(items, where=f"{where}.items"))
    elif isinstance(schema, list):
        for position, entry in enumerate(schema):
            problems.extend(check_schema(entry, where=f"{where}[{position}]"))
    return problems


def assert_supported(schema: Mapping[str, Any], where: str = "schema") -> None:
    """Refuse ``unsupported-schema-keyword`` when the schema outruns the validator.

    A raising entry point of its own, rather than an assertion every caller is
    trusted to remember, is what lets the gate prove the freeze can fail.
    """
    problems = check_schema(schema, where=where)
    if problems:
        raise Refused("unsupported-schema-keyword", "; ".join(sorted(problems)))


def load(path: Path | str) -> Dict[str, Any]:
    """Read a schema document; a non-object document is a hard error."""
    with open(path, encoding="utf-8") as handle:
        schema = json.load(handle)
    if not isinstance(schema, dict):
        raise ValueError(f"{path}: a schema document must be a JSON object")
    return schema


def load_and_refuse(path: Path | str) -> Dict[str, Any]:
    """Load a schema and apply the keyword freeze to it."""
    schema = load(path)
    assert_supported(schema, where=str(path))
    return schema


def _type_matches(instance: Any, expected: str) -> bool:
    if expected in ("integer", "number") and isinstance(instance, bool):
        # bool is an int subclass; a schema demanding a number is not satisfied
        # by a truth value.
        return False
    kind = _TYPE_MAP.get(expected)
    if kind is None:
        return False
    return isinstance(instance, kind)


def _violations(
    instance: Any, schema: Mapping[str, Any], path: str, out: List[str]
) -> None:
    if not isinstance(schema, Mapping):
        return

    declared_type = schema.get("type")
    if isinstance(declared_type, str) and not _type_matches(instance, declared_type):
        out.append(f"{path}: expected {declared_type}, got {type(instance).__name__}")
        return

    if "const" in schema and instance != schema["const"]:
        out.append(f"{path}: must equal {schema['const']!r}")
    enum = schema.get("enum")
    if isinstance(enum, list) and instance not in enum:
        out.append(f"{path}: must be one of {enum!r}")

    if isinstance(instance, str):
        min_length = schema.get("minLength")
        if isinstance(min_length, int) and len(instance) < min_length:
            out.append(f"{path}: shorter than minLength {min_length}")
        max_length = schema.get("maxLength")
        if isinstance(max_length, int) and len(instance) > max_length:
            out.append(f"{path}: longer than maxLength {max_length}")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and not re.search(pattern, instance):
            out.append(f"{path}: does not match {pattern!r}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        minimum = schema.get("minimum")
        if isinstance(minimum, (int, float)) and instance < minimum:
            out.append(f"{path}: below minimum {minimum}")
        maximum = schema.get("maximum")
        if isinstance(maximum, (int, float)) and instance > maximum:
            out.append(f"{path}: above maximum {maximum}")

    if isinstance(instance, Mapping):
        required = schema.get("required")
        if isinstance(required, list):
            for name in required:
                if name not in instance:
                    out.append(f"{path}: missing required property {name!r}")
        properties = schema.get("properties") or {}
        if isinstance(properties, Mapping):
            for name, subschema in properties.items():
                if name in instance:
                    _violations(instance[name], subschema, f"{path}.{name}", out)
        additional = schema.get("additionalProperties")
        if additional is False:
            for name in instance:
                if name not in properties:
                    out.append(f"{path}: unexpected property {name!r}")

    if isinstance(instance, list):
        items = schema.get("items")
        if isinstance(items, Mapping):
            for index, entry in enumerate(instance):
                _violations(entry, items, f"{path}[{index}]", out)


def validate(instance: Any, schema: Mapping[str, Any], where: str = "document") -> List[str]:
    """Every violation of ``schema`` by ``instance`` (empty means valid)."""
    assert_supported(schema, where=where)
    out: List[str] = []
    _violations(instance, schema, where, out)
    return out
