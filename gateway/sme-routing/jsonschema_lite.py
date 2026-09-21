"""A small, dependency-free JSON-Schema (draft-07 subset) validator.

The SME-routing lane runs on the platform standard stack (Python stdlib +
PyYAML) and must stay fully offline, so policy validation cannot lean on the
third-party ``jsonschema`` package. This module implements exactly the keyword
subset ``schema.yaml`` uses and is itself covered by negative tests
(GR-12 / AO-GR-4, no-false-green): every supported keyword genuinely rejects a
document that violates it, and the suite proves it by mutation.

It deliberately mirrors the approach of ``guardrails/policy/schemas.py`` (same
subset, same error style) instead of importing it: one pillar must not reach
into another pillar's private module, and this keeps the lane's dependency
surface at stdlib + PyYAML.

Supported keywords:

* ``type`` (string or list of strings), ``enum``, ``const``
* ``properties`` / ``required`` / ``additionalProperties`` (false or schema)
* ``minProperties`` / ``propertyNames``
* ``items`` (single schema applied to every element)
* ``minLength`` / ``maxLength`` / ``pattern``
* ``minItems`` / ``maxItems`` / ``uniqueItems``
* ``minimum`` / ``maximum`` / ``exclusiveMinimum`` / ``exclusiveMaximum``
  (numeric-form exclusives)
* ``allOf`` / ``anyOf`` / ``oneOf`` / ``not``
* ``$ref`` -- local references only (``#/definitions/...``)

Annotation keywords (``$schema``, ``$id``, ``title``, ``description``,
``examples``, ``$comment``, ``default``) and any other unrecognized keyword are
ignored per the JSON Schema specification; unknown *instance* fields are still
rejected when the schema declares ``additionalProperties: false``.

---knowledge---
module_id: gateway.sme-routing.jsonschema_lite
system: gateway
app: sme-routing
solution_class: pattern
patterns: [dependency-free-validator, draft-07-subset, mirror-not-import]
derives_from: guardrails/policy/schemas.py
owner_sme: orchestrator
tier: L1
interfaces: [validate, SchemaValidationError, subschema]
invariants: "every supported keyword genuinely rejects a document that violates it, proven by mutation (no-false-green)"
gotchas: "it deliberately mirrors guardrails/policy/schemas.py instead of importing it, keeping the lane at stdlib plus PyYAML"
related: ["#149"]
do_not_duplicate: guardrails/policy/schemas.py
---knowledge---
"""

from __future__ import annotations

import re
from typing import Any, List, Mapping, Sequence

__all__ = ["SchemaValidationError", "validate", "subschema"]


class SchemaValidationError(ValueError):
    """One or more schema violations, each with a JSON-pointer-ish path."""

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = list(errors)
        super().__init__("\n".join(f"- {error}" for error in self.errors))


def _json_equal(left: Any, right: Any) -> bool:
    """JSON equality -- booleans are not numbers (``True != 1`` in JSON)."""
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        if set(left) != set(right):
            return False
        return all(_json_equal(value, right[key]) for key, value in left.items())
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return False
        return all(_json_equal(a, b) for a, b in zip(left, right))
    return left == right


def _matches_type(instance: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(instance, Mapping)
    if expected == "array":
        return isinstance(instance, list)
    if expected == "string":
        return isinstance(instance, str)
    if expected == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if expected == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if expected == "boolean":
        return isinstance(instance, bool)
    if expected == "null":
        return instance is None
    return True


def _path_text(path: Sequence[Any]) -> str:
    out = ""
    for token in path:
        if isinstance(token, int):
            out += f"[{token}]"
        elif out:
            out += f".{token}"
        else:
            out += str(token)
    return out or "<root>"


def _resolve_ref(reference: str, root: Any) -> Any:
    """Resolve a local ``#/...`` reference against the root schema."""
    if not isinstance(reference, str) or not reference.startswith("#/"):
        raise SchemaValidationError([f"unsupported non-local $ref {reference!r}"])
    node: Any = root
    for part in reference[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, Mapping) or part not in node:
            raise SchemaValidationError([f"unresolvable $ref {reference!r}"])
        node = node[part]
    return node


def _check_object(instance: Mapping, schema: Mapping, path: List[Any],
                  errors: List[str], root: Any) -> None:
    where = _path_text(path)
    properties = schema.get("properties")
    if properties is not None and not isinstance(properties, Mapping):
        errors.append(f"{where}.properties: must be an object")
        return
    if properties is not None:
        for key, subschema_def in properties.items():
            if key in instance:
                _check(instance[key], subschema_def, path + [key], errors, root)
    for key in schema.get("required", []):
        if key not in instance:
            errors.append(f"{where}: missing required property {key!r}")
    additional = schema.get("additionalProperties", True)
    if additional is False:
        if properties is not None:
            for key in instance:
                if key not in properties:
                    errors.append(
                        f"{where}: additional property {key!r} is not allowed"
                    )
    elif additional is not True:
        for key, value in instance.items():
            if properties is None or key not in properties:
                _check(value, additional, path + [key], errors, root)
    minimum = schema.get("minProperties")
    if minimum is not None:
        if not isinstance(minimum, int) or isinstance(minimum, bool):
            errors.append(f"{where}.minProperties: must be an integer")
        elif len(instance) < minimum:
            errors.append(f"{where}: expected at least {minimum} propert(ies)")
    name_schema = schema.get("propertyNames")
    if name_schema is not None:
        for key in instance:
            _check(key, name_schema, path + [key], errors, root)


def _check_array(instance: list, schema: Mapping, path: List[Any],
                 errors: List[str], root: Any) -> None:
    where = _path_text(path)
    items = schema.get("items")
    if isinstance(items, (Mapping, bool)):
        for index, value in enumerate(instance):
            _check(value, items, path + [index], errors, root)
    for name in ("minItems", "maxItems"):
        limit = schema.get(name)
        if limit is None:
            continue
        if not isinstance(limit, int) or isinstance(limit, bool):
            errors.append(f"{where}.{name}: must be an integer")
        elif name == "minItems" and len(instance) < limit:
            errors.append(f"{where}: expected at least {limit} item(s)")
        elif name == "maxItems" and len(instance) > limit:
            errors.append(f"{where}: expected at most {limit} item(s)")
    if schema.get("uniqueItems"):
        seen: List[Any] = []
        for index, value in enumerate(instance):
            if any(_json_equal(value, previous) for previous in seen):
                errors.append(f"{where}[{index}]: array items must be unique")
                break
            seen.append(value)


def _check_string(instance: str, schema: Mapping, path: List[Any],
                  errors: List[str]) -> None:
    where = _path_text(path)
    for name in ("minLength", "maxLength"):
        limit = schema.get(name)
        if limit is None:
            continue
        if not isinstance(limit, int) or isinstance(limit, bool):
            errors.append(f"{where}.{name}: must be an integer")
        elif name == "minLength" and len(instance) < limit:
            errors.append(f"{where}: string shorter than minLength {limit}")
        elif name == "maxLength" and len(instance) > limit:
            errors.append(f"{where}: string longer than maxLength {limit}")
    pattern = schema.get("pattern")
    if pattern is not None:
        try:
            if re.search(pattern, instance) is None:
                errors.append(f"{where}: string does not match pattern {pattern!r}")
        except re.error as exc:
            errors.append(f"{where}: invalid schema pattern {pattern!r}: {exc}")


def _check_number(instance: Any, schema: Mapping, path: List[Any],
                  errors: List[str]) -> None:
    where = _path_text(path)
    for name, violates in (
        ("minimum", lambda value, bound: value < bound),
        ("maximum", lambda value, bound: value > bound),
        ("exclusiveMinimum", lambda value, bound: value <= bound),
        ("exclusiveMaximum", lambda value, bound: value >= bound),
    ):
        bound = schema.get(name)
        if bound is not None and violates(instance, bound):
            errors.append(f"{where}: value {instance} violates {name} {bound}")


def _check(instance: Any, schema: Any, path: List[Any],
           errors: List[str], root: Any) -> None:
    if isinstance(schema, bool):  # draft-06+ boolean schemas
        if not schema:
            errors.append(f"{_path_text(path)}: boolean schema false rejects every value")
        return
    if not isinstance(schema, Mapping):
        errors.append(f"{_path_text(path)}: schema must be an object or boolean")
        return

    if "$ref" in schema:
        try:
            target = _resolve_ref(schema["$ref"], root)
        except SchemaValidationError as exc:
            errors.append(exc.errors[0])
            return
        _check(instance, target, path, errors, root)
        return

    where = _path_text(path)

    for subschema_def in schema.get("allOf", []):
        _check(instance, subschema_def, path, errors, root)

    for keyword in ("anyOf", "oneOf"):
        options = schema.get(keyword)
        if options is None:
            continue
        passing = 0
        for option in options:
            branch_errors: List[str] = []
            _check(instance, option, path, branch_errors, root)
            if not branch_errors:
                passing += 1
        if keyword == "anyOf" and passing < 1:
            errors.append(f"{where}: no anyOf branch matched")
        if keyword == "oneOf" and passing != 1:
            errors.append(
                f"{where}: expected exactly one oneOf branch to match, {passing} matched"
            )

    if "not" in schema:
        branch_errors = []
        _check(instance, schema["not"], path, branch_errors, root)
        if not branch_errors:
            errors.append(f"{where}: value matches the 'not' schema")

    if "const" in schema and not _json_equal(instance, schema["const"]):
        errors.append(f"{where}: expected const {schema['const']!r}")
    if "enum" in schema and not any(
        _json_equal(instance, option) for option in schema["enum"]
    ):
        errors.append(f"{where}: value {instance!r} not in enum {schema['enum']!r}")

    if "type" in schema:
        expected = schema["type"]
        types = [expected] if isinstance(expected, str) else expected
        if not isinstance(types, list) or not types:
            errors.append(f"{where}: 'type' must be a string or non-empty list")
            return
        if not any(_matches_type(instance, name) for name in types):
            errors.append(
                f"{where}: expected type {types!r}, got {type(instance).__name__}"
            )
            return  # avoid cascading child errors on the wrong type

    if isinstance(instance, Mapping):
        _check_object(instance, schema, path, errors, root)
    elif isinstance(instance, list):
        _check_array(instance, schema, path, errors, root)
    elif isinstance(instance, str):
        _check_string(instance, schema, path, errors)
    elif isinstance(instance, (int, float)) and not isinstance(instance, bool):
        _check_number(instance, schema, path, errors)


def validate(instance: Any, schema: Any, root: Any = None) -> None:
    """Raise ``SchemaValidationError`` listing every violation, or return None.

    ``root`` is the document ``$ref`` pointers resolve against; pass it when
    ``schema`` is a subschema (otherwise the subschema is its own root).
    """
    errors: List[str] = []
    _check(instance, schema, [], errors, schema if root is None else root)
    if errors:
        raise SchemaValidationError(errors)


def subschema(schema: Any, name: str) -> Any:
    """Return ``#/definitions/<name>`` from a loaded schema document."""
    definitions = schema.get("definitions") if isinstance(schema, Mapping) else None
    if not isinstance(definitions, Mapping) or name not in definitions:
        raise SchemaValidationError([f"schema has no definition named {name!r}"])
    return definitions[name]
