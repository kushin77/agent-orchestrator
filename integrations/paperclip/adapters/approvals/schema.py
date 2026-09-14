"""A tiny JSON-Schema subset validator (issue #416).

The adapter takes no third-party dependency, so the projected approval is
checked against ``schema/approval.schema.json`` with this stdlib-only validator.
It covers the keywords that schema uses — ``type``, ``required``,
``properties``, ``additionalProperties``, ``enum``, ``minLength`` — and reports
each violation naming the field. It is deliberately small: a schema the adapter
does not use is a schema the adapter does not need.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

SCHEMA_PATH = Path(__file__).resolve().parent / "schema" / "approval.schema.json"

_TYPE_MAP = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
}


def load_schema(path: Path | str = SCHEMA_PATH) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def validate(instance: Any, schema: Dict[str, Any], where: str = "approval") -> List[str]:
    """Return every violation of ``schema`` by ``instance``; empty means valid."""
    problems: List[str] = []
    expected = schema.get("type")
    if expected is not None:
        kind = _TYPE_MAP.get(expected)
        if kind is not None and not isinstance(instance, kind):
            return [f"{where}: expected {expected}, got {type(instance).__name__}"]
        if expected in ("integer", "number") and isinstance(instance, bool):
            return [f"{where}: expected {expected}, got boolean"]

    if isinstance(instance, dict):
        for field in schema.get("required", []):
            if field not in instance:
                problems.append(f"{where}: missing required field '{field}'")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for field in instance:
                if field not in properties:
                    problems.append(f"{where}: unknown field '{field}'")
        for field, subschema in properties.items():
            if field in instance:
                problems.extend(validate(instance[field], subschema, where=f"{where}.{field}"))
    elif isinstance(instance, str):
        if "enum" in schema and instance not in schema["enum"]:
            problems.append(f"{where}: {instance!r} is not one of {', '.join(map(str, schema['enum']))}")
        minimum = schema.get("minLength")
        if minimum is not None and len(instance) < minimum:
            problems.append(f"{where}: must be at least {minimum} character(s)")
    elif isinstance(instance, list):
        items = schema.get("items")
        if items is not None:
            for position, entry in enumerate(instance):
                problems.extend(validate(entry, items, where=f"{where}[{position}]"))
    return problems
