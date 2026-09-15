"""A stdlib-only JSON-Schema subset validator, and the keyword freeze (issue #650).

This module follows this repository's own convention rather than inventing one
(``governance/modules/schema.py``, ``guardrails/policy``, ``gateway/sme-routing``,
``integrations/paperclip/adapters/approvals/schema.py``): schemas in this repo are
validated by a **stdlib-only JSON-Schema subset** so the gate is offline,
deterministic and dependency-free.

Three properties make the subset worth having:

* **the keyword set is frozen and enforced.** :func:`check_schema` refuses a
  schema that uses a keyword this validator does not implement. An ignored
  keyword is a requirement nobody measures — worse than no schema at all,
  because the artifact claims to be constrained and is not. That refusal is a
  real failure mode, so it has its own code (``unsupported-schema-keyword``).
* **the violation names the row.** :func:`validate` reports
  ``<json path>: <what is wrong>`` for every violation, in document order, so a
  refusal can name the field instead of "the document is invalid".
* **no silent coercion.** Booleans are not integers and ``1`` is not ``"1"``:
  a schema that demands an integer is not satisfied by ``True``, which is a
  subclass of ``int`` in Python and the classic way a type check stops
  measuring.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from .model import Refused

#: Every keyword this validator implements. A schema using anything else is
#: refused rather than silently under-enforced.
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

#: The simple types a declaration may name for a field. Kept apart from
#: ``_TYPE_MAP`` because a *field type* is a narrower promise than a schema
#: ``type``: a declaration may not ask for a number when the caller passes an
#: integer-only value.
SIMPLE_TYPES = ("boolean", "integer", "number", "string")


def check_schema(schema: Any, where: str = "schema") -> List[str]:
    """Every keyword in ``schema`` this validator does not implement (empty is OK)."""
    problems: List[str] = []
    if isinstance(schema, dict):
        for keyword in sorted(schema):
            if keyword not in KEYWORDS:
                problems.append(
                    f"{where}: unsupported keyword {keyword!r} is not enforced by this "
                    f"validator (supported: {', '.join(sorted(KEYWORDS))})"
                )
        for keyword, subschema in schema.get("properties", {}).items():
            problems.extend(check_schema(subschema, where=f"{where}.properties.{keyword}"))
        items = schema.get("items")
        if items is not None:
            problems.extend(check_schema(items, where=f"{where}.items"))
    elif isinstance(schema, list):
        for position, entry in enumerate(schema):
            problems.extend(check_schema(entry, where=f"{where}[{position}]"))
    return problems


def assert_supported(document: Mapping[str, Any], where: str = "schema") -> None:
    """Raise ``unsupported-schema-keyword`` if the schema promises more than this validator does.

    The freeze is only worth having if something can fail on it, so the check
    has a raising entry point of its own rather than being an assertion each
    caller is trusted to remember.
    """
    problems = check_schema(document, where=where)
    if problems:
        raise Refused("unsupported-schema-keyword", "; ".join(sorted_problems(problems)))


def load(path: Path | str) -> Dict[str, Any]:
    """Read a schema document. An unreadable or non-object schema is a hard error."""
    with open(path, encoding="utf-8") as handle:
        schema = json.load(handle)
    if not isinstance(schema, dict):
        raise ValueError(f"{path}: a schema document must be a JSON object")
    return schema


def _type_matches(instance: Any, expected: str) -> bool:
    if expected == "integer" or expected == "number":
        # bool is an int subclass; a schema that demands a number is not
        # satisfied by a truth value.
        if isinstance(instance, bool):
            return False
    kind = _TYPE_MAP.get(expected)
    if kind is None:
        return False
    return isinstance(instance, kind)


def validate(instance: Any, schema: Mapping[str, Any], where: str = "document") -> List[str]:
    """Return every violation of ``schema`` by ``instance``; empty means valid."""
    problems: List[str] = []

    expected = schema.get("type")
    if expected is not None and not _type_matches(instance, expected):
        return [f"{where}: expected {expected}, got {type(instance).__name__}"]

    if "const" in schema and instance != schema["const"]:
        problems.append(f"{where}: must be {schema['const']!r}")

    if isinstance(instance, dict):
        for name in schema.get("required", []):
            if name not in instance:
                problems.append(f"{where}: missing required field '{name}'")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for name in instance:
                if name not in properties:
                    problems.append(f"{where}: unknown field '{name}'")
        for name, subschema in properties.items():
            if name in instance:
                problems.extend(validate(instance[name], subschema, where=f"{where}.{name}"))
    elif isinstance(instance, str):
        if "enum" in schema and instance not in schema["enum"]:
            problems.append(
                f"{where}: {instance!r} is not one of {', '.join(map(str, schema['enum']))}"
            )
        minimum = schema.get("minLength")
        if minimum is not None and len(instance) < minimum:
            problems.append(f"{where}: must be at least {minimum} character(s)")
        maximum = schema.get("maxLength")
        if maximum is not None and len(instance) > maximum:
            problems.append(f"{where}: must be at most {maximum} character(s)")
        pattern = schema.get("pattern")
        if pattern is not None and re.search(pattern, instance) is None:
            problems.append(f"{where}: {instance!r} does not match {pattern!r}")
    elif isinstance(instance, list):
        items = schema.get("items")
        if items is not None:
            for position, entry in enumerate(instance):
                problems.extend(validate(entry, items, where=f"{where}[{position}]"))
    elif isinstance(instance, (int, float)) and not isinstance(instance, bool):
        minimum = schema.get("minimum")
        if minimum is not None and instance < minimum:
            problems.append(f"{where}: must be >= {minimum}")
        maximum = schema.get("maximum")
        if maximum is not None and instance > maximum:
            problems.append(f"{where}: must be <= {maximum}")
    return problems


def validate_named(
    instance: Any,
    schema: Mapping[str, Any],
    where: str,
    *,
    schema_label: str = "schema",
) -> List[str]:
    """``validate`` plus the keyword freeze, so an unenforceable schema is refused.

    A caller that runs ``validate`` alone is trusting a schema it has not
    audited. This entry point refuses an unsupported keyword *first*: the
    schema's own defects are reported instead of the instance's, because an
    instance cannot be judged by a promise nothing enforces.
    """
    problems = check_schema(schema, where=schema_label)
    if problems:
        return problems
    return validate(instance, schema, where=where)


def field_type_problems(
    value: Any,
    expected: str,
    where: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> List[str]:
    """Check one declared field value against its declared simple type."""
    problems: List[str] = []
    if expected not in SIMPLE_TYPES:
        return [f"{where}: unknown field type {expected!r} (known: {', '.join(SIMPLE_TYPES)})"]
    if not _type_matches(value, expected):
        return [f"{where}: expected {expected}, got {type(value).__name__}"]
    if expected in ("integer", "number"):
        if minimum is not None and value < minimum:
            problems.append(f"{where}: must be >= {minimum}")
        if maximum is not None and value > maximum:
            problems.append(f"{where}: must be <= {maximum}")
    return problems


def sorted_problems(problems: Sequence[str]) -> List[str]:
    """A stable, de-duplicated ordering so a refusal's text is deterministic."""
    return sorted(dict.fromkeys(problems))
