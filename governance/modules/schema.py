"""The frozen registry schema, and the validator that holds the generator to it (issue #591).

---knowledge---
module_id: governance.modules.schema
system: governance
app: modules
solution_class: enterprise
patterns: [honesty-tri-state, offline-hermetic, deterministic]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [SchemaUnavailable, SchemaViolation, load, check_schema, problems, validate]
invariants: ""
gotchas: ""
related: ["#447", "#591"]
do_not_duplicate: null
---knowledge---

The registry's row shape was implicit in ``registry.py`` and promised only in
prose (``docs/MODULE-REGISTRY.md``): #447 already composes a summary from it and
later consumers will read it, so the shape is frozen as an artifact
(``module-registry.schema.json``) and **the generator validates what it emits**
against it before the document leaves ``registry.build``.

Two disciplines make the freeze worth something:

* **no third-party validator.** ``guardrails/policy`` and ``gateway/sme-routing``
  established the convention: this repository's schemas are validated by a
  stdlib-only JSON-Schema *subset* validator, so the gate is offline and
  deterministic. :func:`check_schema` refuses a schema that uses a keyword this
  validator does not implement — an ignored keyword would be a requirement nobody
  measures, which is worse than no schema at all. ``tests/test_schema.py``
  cross-checks this validator against the real ``jsonschema`` package when it is
  importable, so the subset is not trusted on its own word.
* **the violation names the row.** :func:`problems` reports
  ``<json path>: <what is wrong>`` for every violation, sorted, so a refusal can
  name the entry and the field instead of "the document is invalid".

Exit-code contract (repository convention): an unreadable or unsupported schema,
and a document that violates it, both raise a
:class:`~governance.modules.model.CannotAssess` — the CLI maps it to exit 2.
An emitted document that violates its own schema is CANNOT-ASSESS, never a pass:
the registry cannot be assessed by a shape it cannot satisfy.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from governance.modules.model import CannotAssess

#: The packaged schema, applied to every build unless a caller names another.
DEFAULT_SCHEMA = Path(__file__).resolve().parent / "module-registry.schema.json"

#: The JSON-Schema draft the artifact declares.
DIALECT = "https://json-schema.org/draft/2020-12/schema"

#: Every keyword this validator implements. A schema using anything else is
#: refused rather than silently under-enforced.
KEYWORDS = frozenset(
    {
        "$schema",
        "$id",
        "$comment",
        "title",
        "description",
        "$defs",
        "$ref",
        "type",
        "required",
        "properties",
        "additionalProperties",
        "items",
        "enum",
        "const",
        "minItems",
        "maxItems",
        "uniqueItems",
    }
)

#: Keywords whose value is itself a schema, or a map of schemas.
_SCHEMA_MAP = ("properties", "$defs")
_SCHEMA_VALUE = ("items", "additionalProperties")

_TYPES = {
    "object": lambda value: isinstance(value, dict),
    "array": lambda value: isinstance(value, list),
    "string": lambda value: isinstance(value, str),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "boolean": lambda value: isinstance(value, bool),
    "null": lambda value: value is None,
}


class SchemaUnavailable(CannotAssess):
    """The schema is missing, malformed, or uses a keyword this validator cannot enforce."""


class SchemaViolation(CannotAssess):
    """The document does not satisfy the frozen schema."""


# --------------------------------------------------------------------------- #
# the schema itself
# --------------------------------------------------------------------------- #
def load(path: Optional[Path] = None) -> Dict[str, Any]:
    """Read, parse and *check* the schema. Never cached: a stale copy of a schema
    is how a gate certifies a shape that is no longer on disk."""
    path = Path(path) if path else DEFAULT_SCHEMA
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SchemaUnavailable("the frozen schema {} is unreadable: {}".format(path, exc)) from exc
    try:
        schema = json.loads(text)
    except ValueError as exc:
        raise SchemaUnavailable("the frozen schema {} is not JSON: {}".format(path, exc)) from exc
    check_schema(schema, path)
    return schema


def check_schema(schema: Any, path: Optional[Path] = None) -> None:
    """Refuse a schema this validator cannot fully enforce."""
    path = Path(path) if path else DEFAULT_SCHEMA
    if not isinstance(schema, Mapping):
        raise SchemaUnavailable("the frozen schema {} is not an object".format(path))
    if str(schema.get("$schema") or "") != DIALECT:
        raise SchemaUnavailable(
            "the frozen schema {} declares dialect {!r}, expected {!r}".format(
                path, schema.get("$schema"), DIALECT
            )
        )
    _walk_schema(schema, schema, "#", path)


def _walk_schema(node: Any, root: Any, where: str, path: Path) -> None:
    if not isinstance(node, Mapping):
        raise SchemaUnavailable(
            "the frozen schema {} declares {} as a non-object subschema".format(path, where)
        )
    for keyword, value in node.items():
        if keyword not in KEYWORDS:
            raise SchemaUnavailable(
                "the frozen schema {} uses the keyword {!r} at {}, which this "
                "validator does not implement — an unenforced keyword is a "
                "requirement nobody measures".format(path, keyword, where)
            )
        if keyword == "$ref":
            _resolve(str(value), root, where, path)
        elif keyword in _SCHEMA_MAP:
            if not isinstance(value, Mapping):
                raise SchemaUnavailable(
                    "the frozen schema {} declares {} at {} as a non-object".format(
                        path, keyword, where
                    )
                )
            for name, sub in value.items():
                _walk_schema(sub, root, "{}/{}/{}".format(where, keyword, name), path)
        elif keyword in _SCHEMA_VALUE:
            if isinstance(value, bool):
                continue
            _walk_schema(value, root, "{}/{}".format(where, keyword), path)
        elif keyword == "type":
            names = value if isinstance(value, list) else [value]
            for name in names:
                if str(name) not in _TYPES:
                    raise SchemaUnavailable(
                        "the frozen schema {} declares the unknown type {!r} at {}".format(
                            path, name, where
                        )
                    )


def _resolve(ref: str, root: Any, where: str, path: Optional[Path] = None) -> Any:
    if not ref.startswith("#/"):
        raise SchemaUnavailable(
            "the frozen schema {} refers to {!r} at {}, which is not a local "
            "pointer".format(path or DEFAULT_SCHEMA, ref, where)
        )
    node = root
    for part in ref[2:].split("/"):
        if not isinstance(node, Mapping) or part not in node:
            raise SchemaUnavailable(
                "the frozen schema {} refers to {!r} at {}, which does not "
                "exist".format(path or DEFAULT_SCHEMA, ref, where)
            )
        node = node[part]
    return node


# --------------------------------------------------------------------------- #
# validating a document
# --------------------------------------------------------------------------- #
def _where(parts: Sequence[Any]) -> str:
    if not parts:
        return "(document)"
    return "/".join(str(part) for part in parts)


def _same(value: Any, expected: Any) -> bool:
    """JSON equality, so ``True`` is not ``1`` and ``False`` is not ``0``."""
    if isinstance(value, bool) or isinstance(expected, bool):
        return isinstance(value, bool) and isinstance(expected, bool) and value == expected
    if isinstance(value, (int, float)) and isinstance(expected, (int, float)):
        return not isinstance(value, bool) and not isinstance(expected, bool) and value == expected
    return type(value) is type(expected) and value == expected


def _validate(
    value: Any,
    schema: Any,
    root: Any,
    parts: List[Any],
    problems: List[str],
) -> None:
    if not isinstance(schema, Mapping):
        return
    if "$ref" in schema:
        _validate(value, _resolve(str(schema["$ref"]), root, _where(parts)), root, parts, problems)

    declared_type = schema.get("type")
    if declared_type is not None:
        names = declared_type if isinstance(declared_type, list) else [declared_type]
        if not any(_TYPES[str(name)](value) for name in names):
            problems.append(
                "{}: expected {}, found {}".format(
                    _where(parts), " or ".join(str(name) for name in names), _kind(value)
                )
            )
            return

    if "const" in schema and not _same(value, schema["const"]):
        problems.append(
            "{}: expected {!r}, found {!r}".format(_where(parts), schema["const"], value)
        )

    if "enum" in schema:
        allowed = list(schema["enum"])
        if not any(_same(value, item) for item in allowed):
            problems.append(
                "{}: {!r} is not one of {}".format(_where(parts), value, allowed)
            )

    if isinstance(value, Mapping):
        required = schema.get("required") or []
        for key in required:
            if key not in value:
                problems.append(
                    "{}: the required property {!r} is missing".format(_where(parts), key)
                )
        properties = schema.get("properties") or {}
        for key, sub in properties.items():
            if key in value:
                _validate(value[key], sub, root, parts + [key], problems)
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in properties:
                    problems.append(
                        "{}: the property {!r} is not declared (the shape is "
                        "frozen)".format(_where(parts), key)
                    )
        elif isinstance(schema.get("additionalProperties"), Mapping):
            for key in value:
                if key not in properties:
                    _validate(
                        value[key], schema["additionalProperties"], root, parts + [key], problems
                    )

    if isinstance(value, list):
        if "minItems" in schema and len(value) < int(schema["minItems"]):
            problems.append(
                "{}: expected at least {} item(s), found {}".format(
                    _where(parts), schema["minItems"], len(value)
                )
            )
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            problems.append(
                "{}: expected at most {} item(s), found {}".format(
                    _where(parts), schema["maxItems"], len(value)
                )
            )
        if schema.get("uniqueItems"):
            seen = {_fingerprint(item) for item in value}
            if len(seen) != len(value):
                problems.append("{}: the items are not unique".format(_where(parts)))
        items = schema.get("items")
        if items is not None:
            for index, item in enumerate(value):
                _validate(item, items, root, parts + [index], problems)


def _fingerprint(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):  # pragma: no cover - documents are JSON already
        return repr(value)


def _kind(value: Any) -> str:
    for name in ("boolean", "integer", "number", "string", "array", "object"):
        if _TYPES[name](value):
            return name if name != "integer" or not isinstance(value, bool) else "boolean"
    return "null" if value is None else type(value).__name__


def problems(document: Any, schema: Mapping[str, Any]) -> Tuple[str, ...]:
    """Every violation, as ``<json path>: <what is wrong>``, sorted."""
    found: List[str] = []
    _validate(document, schema, schema, [], found)
    return tuple(sorted(set(found)))


def validate(document: Any, path: Optional[Path] = None) -> None:
    """Raise :class:`SchemaViolation` unless the document satisfies the schema.

    Called by ``registry.build`` on the document it is about to return: the
    generator validates what it emits, rather than a test asserting it later.
    """
    schema_path = Path(path) if path else DEFAULT_SCHEMA
    schema = load(schema_path)
    found = problems(document, schema)
    if not found:
        return
    shown = "; ".join(found[:3])
    if len(found) > 3:
        shown += " (+{} more)".format(len(found) - 3)
    raise SchemaViolation(
        "the emitted registry document does not satisfy the frozen schema {}: {} "
        "violation(s): {}".format(schema_path, len(found), shown)
    )
