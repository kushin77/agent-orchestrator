"""Minimal, honest JSON Schema (draft-07 subset) validator for the contract.

The sandbox security-profile contract is declared as JSON Schema
(``security-profile.schema.json``, ``profiles.schema.json``,
``categories.schema.json``, ``microvm.schema.json``) and instantiated as YAML
(``profiles.yaml``, ``categories.yaml``). This module implements the subset of
draft-07 those schemas need - type / enum / const / required / properties /
additionalProperties / items / minItems / uniqueItems / minimum / maximum /
pattern and local ``#/definitions/NAME`` references - with real failing
paths: an unknown network mode, a missing required field, an extra key or an
out-of-range quota all produce errors, so the contract is enforced offline
with no third-party dependency. It is a validator, not a formality: a document
that validates against a schema that forbids something genuinely fails when it
contains that thing.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Mapping, Tuple

_SCHEMA_DIR = os.path.dirname(os.path.abspath(__file__))

# Keywords this validator implements. Everything else that draft-07 allows as
# an annotation ($schema, $id, $comment, title, description, default, ...) is
# ignored; anything that would change validity but is not implemented here is
# refused loudly by :func:`validate_document` (fail closed, no silent pass).
_IMPLEMENTED = frozenset(
    {
        "type",
        "enum",
        "const",
        "required",
        "properties",
        "additionalProperties",
        "items",
        "minItems",
        "uniqueItems",
        "minimum",
        "maximum",
        "pattern",
        "$ref",
        "definitions",
    }
)
_ANNOTATIONS = frozenset(
    {
        "$schema",
        "$id",
        "title",
        "description",
        "default",
        "examples",
        "$comment",
    }
)


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, Mapping)
    if expected == "array":
        return isinstance(value, (list, tuple))
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        # bool is a subclass of int in python; JSON booleans are not integers.
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "null":
        return value is None
    return False


def _resolve_ref(ref: str, root: Mapping[str, Any]) -> Tuple[Any, str]:
    """Resolve a local ``#/definitions/NAME`` reference against ``root``.

    Returns ``(subschema, error)`` - exactly one of the two is non-empty.
    """
    if not ref.startswith("#/definitions/"):
        return None, f"unsupported $ref {ref!r} (only #/definitions/NAME)"
    name = ref[len("#/definitions/"):]
    definitions = root.get("definitions")
    if not isinstance(definitions, Mapping) or name not in definitions:
        return None, f"$ref {ref!r} does not resolve to a definition"
    return definitions[name], ""


def _check(
    instance: Any,
    schema: Mapping[str, Any],
    path: str,
    errors: List[str],
    root: Mapping[str, Any],
) -> None:
    if not isinstance(schema, Mapping):
        errors.append(f"{path}: schema is not an object")
        return

    if "$ref" in schema:
        target, ref_error = _resolve_ref(schema["$ref"], root)
        if ref_error:
            errors.append(f"{path}: {ref_error}")
            return
        return _check(instance, target, path, errors, root)

    unsupported = sorted(
        set(schema) - _IMPLEMENTED - _ANNOTATIONS - {"$ref", "definitions"}
    )
    if unsupported:
        errors.append(
            f"{path}: unsupported JSON Schema keyword(s) {unsupported} "
            f"(validator implements: {sorted(_IMPLEMENTED)})"
        )
        return

    declared = schema.get("type")
    if declared is not None:
        allowed = [declared] if isinstance(declared, str) else list(declared)
        if not any(_type_matches(instance, t) for t in allowed):
            errors.append(
                f"{path}: expected type {allowed!r}, got "
                f"{type(instance).__name__}"
            )
            return  # the other keywords only apply once the type matches

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(
            f"{path}: value {instance!r} not in enum {schema['enum']!r}"
        )

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}")

    if isinstance(instance, Mapping):
        if "required" in schema:
            for key in schema["required"]:
                if key not in instance:
                    errors.append(f"{path}: missing required field {key!r}")
        properties = schema.get("properties")
        if isinstance(properties, Mapping):
            for key, subschema in properties.items():
                if key in instance:
                    _check(
                        instance[key],
                        subschema,
                        f"{path}.{key}" if path else key,
                        errors,
                        root,
                    )
        extra = [key for key in instance if key not in (properties or {})]
        if extra:
            additional = schema.get("additionalProperties", True)
            if additional is False:
                errors.append(
                    f"{path}: additional property {extra[0]!r} not allowed"
                )
            elif isinstance(additional, Mapping):
                for key in extra:
                    _check(
                        instance[key],
                        additional,
                        f"{path}.{key}" if path else key,
                        errors,
                        root,
                    )

    if isinstance(instance, (list, tuple)):
        if "items" in schema and isinstance(schema["items"], Mapping):
            for index, item in enumerate(instance):
                _check(item, schema["items"], f"{path}[{index}]", errors, root)
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(
                f"{path}: expected at least {schema['minItems']} item(s), "
                f"got {len(instance)}"
            )
        if schema.get("uniqueItems") and len(set(instance)) != len(instance):
            errors.append(f"{path}: items are not unique")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(
                f"{path}: value {instance!r} is below minimum "
                f"{schema['minimum']}"
            )
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(
                f"{path}: value {instance!r} is above maximum "
                f"{schema['maximum']}"
            )

    if isinstance(instance, str) and "pattern" in schema:
        if re.search(schema["pattern"], instance) is None:
            errors.append(f"{path}: value {instance!r} does not match pattern")


def validate_document(instance: Any, schema: Mapping[str, Any]) -> List[str]:
    """Validate ``instance`` against ``schema``; return a list of errors.

    An empty list means valid. Unsupported keywords and unresolvable refs are
    themselves errors (fail closed) so a schema this validator cannot fully
    honour is never silently treated as valid.
    """
    if not isinstance(schema, Mapping):
        return ["schema must be an object"]
    errors: List[str] = []
    _check(instance, schema, "$", errors, schema)
    return errors


def is_valid(instance: Any, schema: Mapping[str, Any]) -> bool:
    return not validate_document(instance, schema)


def load_schema(name: str) -> Dict[str, Any]:
    """Load one of the packaged contract schemas (``<name>.schema.json``)."""
    path = os.path.join(_SCHEMA_DIR, f"{name}.schema.json")
    with open(path, "r", encoding="utf-8") as handle:
        doc = json.load(handle)
    if not isinstance(doc, Mapping):
        raise ValueError(f"{path}: schema document is not an object")
    return dict(doc)


def validate_document_file(instance: Any, schema_name: str) -> List[str]:
    """Validate against a packaged schema by its file stem."""
    return validate_document(instance, load_schema(schema_name))
