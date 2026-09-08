"""A small, dependency-free JSON-Schema (draft-07 subset) validator.

The guardrail policy lane runs on the platform standard stack (Python stdlib
+ PyYAML) and must stay fully offline and deterministic, so startup
validation cannot lean on the third-party ``jsonschema`` package.  This module
implements the keyword subset the lane's schema files use and is itself
covered by negative tests (AO-GR-4, no-false-green): every supported keyword
genuinely rejects a document that violates it.

Supported keywords:

* ``type`` (string or list of strings), ``enum``, ``const``
* ``properties`` / ``required`` / ``additionalProperties`` (false or schema)
* ``items`` (single schema applied to every element)
* ``minLength`` / ``maxLength`` / ``pattern``
* ``minItems`` / ``maxItems`` / ``uniqueItems``
* ``minimum`` / ``maximum`` / ``exclusiveMinimum`` / ``exclusiveMaximum``
  (numeric-form exclusives)
* ``allOf`` / ``anyOf`` / ``oneOf`` / ``not``
* ``$ref`` — local references only (``#/definitions/...``)

Annotation keywords (``$schema``, ``$id``, ``title``, ``description``,
``examples``) and any other unrecognized keyword are ignored per the JSON
Schema specification; unknown *instance* fields are still rejected when the
schema declares ``additionalProperties: false``.
"""

from __future__ import annotations

import json
import re
from typing import Any, List, Mapping, Sequence


class SchemaValidationError(ValueError):
    """One or more schema violations, each with a JSON pointer-ish path."""

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = list(errors)
        super().__init__("\n".join(f"- {error}" for error in self.errors))


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


def _ref_target(reference: str, root: Any) -> Any:
    """Resolve a local ``#/definitions/...`` reference against the root schema."""
    if not reference.startswith("#/"):
        raise SchemaValidationError([f"unsupported non-local $ref {reference!r}"])
    node: Any = root
    for part in reference[2:].split("/"):
        if not isinstance(node, Mapping) or part not in node:
            raise SchemaValidationError([f"unresolvable $ref {reference!r}"])
        node = node[part]
    return node


def _check(
    instance: Any,
    schema: Any,
    path: List[Any],
    errors: List[str],
    root: Any,
) -> None:
    if isinstance(schema, bool):  # draft-06+ boolean schemas
        if not schema:
            errors.append(f"{_path_text(path)}: boolean schema false rejects every value")
        return
    if not isinstance(schema, Mapping):
        errors.append(f"{_path_text(path)}: schema must be an object or boolean")
        return

    where = _path_text(path)

    if "$ref" in schema:
        try:
            target = _ref_target(schema["$ref"], root)
        except SchemaValidationError as exc:
            errors.append(exc.errors[0])
            return
        _check(instance, target, path, errors, root)
        return

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

    # --- object keywords ---------------------------------------------------
    if isinstance(instance, Mapping):
        properties = schema.get("properties")
        if properties is not None:
            if not isinstance(properties, Mapping):
                errors.append(f"{where}.properties: must be an object")
                return
            for key, subschema in properties.items():
                if key in instance:
                    _check(instance[key], subschema, path + [key], errors, root)
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{where}: missing required property {key!r}")
        additional = schema.get("additionalProperties", True)
        if additional is not False and additional is not True:
            for key, value in instance.items():
                if properties is None or key not in properties:
                    _check(value, additional, path + [key], errors, root)
        elif additional is False and properties is not None:
            for key in instance:
                if key not in properties:
                    errors.append(f"{where}: additional property {key!r} is not allowed")

    # --- array keywords ----------------------------------------------------
    if isinstance(instance, list):
        items = schema.get("items")
        if isinstance(items, Mapping) or isinstance(items, bool):
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
                if any(_strict_json_equal(value, previous) for previous in seen):
                    errors.append(f"{where}[{index}]: array items must be unique")
                    break
                seen.append(value)

    # --- scalar keywords ---------------------------------------------------
    if isinstance(instance, str):
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

    # --- numeric keywords --------------------------------------------------
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        for name, op in (
            ("minimum", lambda a, b: a < b),
            ("maximum", lambda a, b: a > b),
        ):
            bound = schema.get(name)
            if bound is not None and op(instance, bound):
                errors.append(f"{where}: value {instance} violates {name} {bound}")
        for name, op in (
            ("exclusiveMinimum", lambda a, b: a <= b),
            ("exclusiveMaximum", lambda a, b: a >= b),
        ):
            bound = schema.get(name)
            if bound is not None and not isinstance(bound, bool) and op(instance, bound):
                errors.append(f"{where}: value {instance} violates {name} {bound}")

    # --- enum / const ------------------------------------------------------
    if "enum" in schema:
        allowed = schema["enum"]
        if not isinstance(allowed, list):
            errors.append(f"{where}.enum: must be a list")
        elif not any(_strict_json_equal(instance, candidate) for candidate in allowed):
            errors.append(f"{where}: value not in enum {allowed!r}")
    if "const" in schema and not _strict_json_equal(instance, schema["const"]):
        errors.append(f"{where}: value does not equal const {schema['const']!r}")

    # --- composition -------------------------------------------------------
    for key, mode in (("allOf", "all"), ("anyOf", "any"), ("oneOf", "one")):
        branches = schema.get(key)
        if branches is None:
            continue
        if not isinstance(branches, list):
            errors.append(f"{where}.{key}: must be a list of schemas")
            continue
        branch_errors: List[List[str]] = []
        for index, branch in enumerate(branches):
            collected: List[str] = []
            _check(instance, branch, path, collected, root)
            branch_errors.append(collected)
        if mode == "all" and any(branch_errors):
            errors.append(f"{where}: failed allOf ({sum(bool(e) for e in branch_errors)} branch(es) failed)")
        elif mode == "any" and all(branch_errors):
            errors.append(f"{where}: failed anyOf (no branch matched)")
        elif mode == "one" and sum(bool(e) for e in branch_errors) != 1:
            errors.append(f"{where}: failed oneOf (matched {sum(bool(e) for e in branch_errors)} branch(es), want exactly 1)")
    if "not" in schema:
        collected: List[str] = []
        _check(instance, schema["not"], path, collected, root)
        if not collected:
            errors.append(f"{where}: value matches the 'not' schema")


def _strict_json_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left is right
    return left == right


def validate(instance: Any, schema: Any) -> None:
    """Validate *instance* against *schema*.

    Raises :class:`SchemaValidationError` (aggregating every violation) when
    the instance does not conform.
    """
    errors: List[str] = []
    _check(instance, schema, [], errors, schema)
    if errors:
        raise SchemaValidationError(errors)


def load_schema(path: str) -> Any:
    """Load a JSON Schema file and return the parsed schema object."""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


#: Canonical schema files shipped with the lane (package-relative paths).
POLICY_SCHEMA_PATH = "schema/policy.schema.json"
CONTROLS_SCHEMA_PATH = "schema/controls.schema.json"
