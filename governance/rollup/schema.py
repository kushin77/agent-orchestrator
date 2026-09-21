"""Fail-closed JSON-Schema validation for the roll-up inputs (issue #151).

---knowledge---
module_id: governance.rollup.schema
system: governance
app: rollup
solution_class: pattern
patterns: [no-false-green, fail-closed, deterministic]
derives_from: null
owner_sme: unassigned
tier: L1
interfaces: [SchemaUnsupported, InputInvalid, check_keywords, definition, validate]
invariants: ""
gotchas: ""
related: ["#151"]
do_not_duplicate: null
---knowledge---

The roll-up is only as trustworthy as its inputs, so the inputs are validated
against ``governance/rollup/schema.yaml`` before any aggregate is computed. Two
properties matter more than coverage:

* **Fail closed on the schema.** A validator that ignores a keyword it does not
  implement reports every document as valid, which is exactly the false green
  this repo rejects (GR-12). The keyword set is therefore closed: a schema that
  uses a keyword outside it raises ``SchemaUnsupported`` and the caller reports
  CANNOT-ASSESS — never a pass.
* **Fail loudly on the document.** Every violation is collected with a JSON
  pointer-ish path (``$.tenants[0].repos[2]``) so the operator sees which
  declaration is wrong, not just that something is.

Only a deliberate subset of draft-07 is implemented (the subset
``schema.yaml`` uses). That is a tradeoff made explicit rather than hidden: the
subset is enforced, the rest is refused.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence

# The closed keyword set. Anything outside it is refused, not ignored.
SUPPORTED_KEYWORDS = frozenset(
    {
        "$schema",
        "$id",
        "title",
        "description",
        "definitions",
        "$ref",
        "type",
        "required",
        "properties",
        "additionalProperties",
        "items",
        "enum",
        "const",
        "pattern",
        "minimum",
        "maximum",
        "minItems",
        "minLength",
        "uniqueItems",
    }
)

_TYPES = ("object", "array", "string", "number", "integer", "boolean")


class SchemaUnsupported(Exception):
    """The schema uses a keyword this validator does not implement."""


class InputInvalid(Exception):
    """A document does not satisfy the schema (or is not a mapping at all)."""


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "number":
        return _is_number(value)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    return False


def _walk_keywords(node: Any, path: str, unsupported: List[str], seen: set) -> None:
    """Check every schema node's keywords, structurally.

    Recursion follows the schema structure (``properties`` values, ``items``,
    ``definitions`` values, schema-valued ``additionalProperties``, and resolved
    local ``$ref`` targets), never the data — a property *name* is not a keyword.
    """
    if not isinstance(node, dict) or id(node) in seen:
        return
    seen.add(id(node))

    for key in sorted(node):
        if key not in SUPPORTED_KEYWORDS:
            unsupported.append("%s.%s" % (path, key))

    properties = node.get("properties")
    if isinstance(properties, dict):
        for name, sub in properties.items():
            _walk_keywords(sub, "%s.properties.%s" % (path, name), unsupported, seen)

    items = node.get("items")
    if isinstance(items, dict):
        _walk_keywords(items, "%s.items" % path, unsupported, seen)

    definitions = node.get("definitions")
    if isinstance(definitions, dict):
        for name, sub in definitions.items():
            _walk_keywords(sub, "%s.definitions.%s" % (path, name), unsupported, seen)

    additional = node.get("additionalProperties")
    if isinstance(additional, dict):
        _walk_keywords(additional, "%s.additionalProperties" % path, unsupported, seen)


def check_keywords(document: Dict[str, Any], root: Dict[str, Any]) -> None:
    """Raise ``SchemaUnsupported`` if any schema node uses an unknown keyword."""
    unsupported: List[str] = []
    _walk_keywords(document, "$", unsupported, set())
    # Follow every $ref the document makes, so a definition body that is only
    # reachable through a reference is checked too.
    pending = _collect_refs(document)
    resolved = set()
    while pending:
        ref = pending.pop()
        if ref in resolved:
            continue
        resolved.add(ref)
        target = _resolve_ref(ref, root)
        _walk_keywords(target, ref, unsupported, set())
        pending.extend(_collect_refs(target))
    if unsupported:
        raise SchemaUnsupported(
            "unsupported schema keyword(s): %s" % ", ".join(sorted(set(unsupported)))
        )


def _collect_refs(node: Any) -> List[str]:
    refs: List[str] = []
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            refs.append(ref)
        for value in node.values():
            refs.extend(_collect_refs(value))
    elif isinstance(node, list):
        for value in node:
            refs.extend(_collect_refs(value))
    return refs


def _resolve_ref(ref: str, root: Dict[str, Any]) -> Any:
    if not ref.startswith("#/"):
        raise SchemaUnsupported("only local $ref is supported, got %r" % ref)
    node: Any = root
    for part in ref[2:].split("/"):
        if not isinstance(node, dict) or part not in node:
            raise SchemaUnsupported("unresolvable $ref %r" % ref)
        node = node[part]
    return node


def definition(root: Dict[str, Any], name: str) -> Dict[str, Any]:
    """The named ``#/definitions/<name>`` subschema, or raise."""
    definitions = root.get("definitions")
    if not isinstance(definitions, dict) or name not in definitions:
        raise SchemaUnsupported("schema defines no %r" % name)
    node = definitions[name]
    if not isinstance(node, dict):
        raise SchemaUnsupported("definition %r is not a schema object" % name)
    return node


def validate(document: Any, schema: Dict[str, Any], root: Dict[str, Any]) -> List[str]:
    """Return every violation of ``document`` against ``schema``.

    An empty list means the document satisfies the schema. Violations are
    reported in a deterministic order so two runs of the same document produce
    the same report.
    """
    violations: List[str] = []
    _validate(document, schema, root, "$", violations)
    return violations


def _validate(
    value: Any, schema: Dict[str, Any], root: Dict[str, Any], path: str, out: List[str]
) -> None:
    if "$ref" in schema:
        target = _resolve_ref(schema["$ref"], root)
        # Sibling keywords next to $ref are not honoured by draft-07; refusing
        # them is safer than silently ignoring them.
        extra = [k for k in schema if k != "$ref"]
        if extra:
            raise SchemaUnsupported("keywords beside $ref: %s" % ", ".join(sorted(extra)))
        _validate(value, target, root, path, out)
        return

    if "const" in schema and value != schema["const"]:
        out.append("%s: must be %r" % (path, schema["const"]))
    if "enum" in schema and value not in schema["enum"]:
        out.append("%s: must be one of %s" % (path, sorted(schema["enum"])))

    expected = schema.get("type")
    if expected is not None:
        if expected not in _TYPES:
            raise SchemaUnsupported("unknown type %r at %s" % (expected, path))
        if not _type_matches(value, expected):
            out.append("%s: expected %s" % (path, expected))
            return

    if isinstance(value, str):
        if "pattern" in schema and not re.search(schema["pattern"], value):
            out.append("%s: does not match %s" % (path, schema["pattern"]))
        if "minLength" in schema and len(value) < schema["minLength"]:
            out.append("%s: shorter than %d" % (path, schema["minLength"]))

    if _is_number(value):
        if "minimum" in schema and value < schema["minimum"]:
            out.append("%s: below minimum %s" % (path, schema["minimum"]))
        if "maximum" in schema and value > schema["maximum"]:
            out.append("%s: above maximum %s" % (path, schema["maximum"]))

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            out.append("%s: fewer than %d item(s)" % (path, schema["minItems"]))
        if schema.get("uniqueItems"):
            seen: List[Any] = []
            for item in value:
                if item in seen:
                    out.append("%s: duplicate item %r" % (path, item))
                seen.append(item)
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                _validate(item, items, root, "%s[%d]" % (path, index), out)

    if isinstance(value, dict):
        required: Sequence[str] = schema.get("required", ())
        for name in required:
            if name not in value:
                out.append("%s: missing required property %r" % (path, name))
        properties = schema.get("properties", {})
        for name in sorted(value):
            if name in properties:
                _validate(value[name], properties[name], root, "%s.%s" % (path, name), out)
            else:
                additional = schema.get("additionalProperties", True)
                if additional is False:
                    out.append("%s: unexpected property %r" % (path, name))
                elif isinstance(additional, dict):
                    _validate(value[name], additional, root, "%s.%s" % (path, name), out)
