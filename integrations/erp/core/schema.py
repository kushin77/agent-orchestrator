"""A stdlib JSON-Schema subset validator for the ERP document model (#647).

The core document model ships its document families as JSON Schema, and the
model must be usable without a deployment dependency: the fleet's own
precedent (`integrations/paperclip/adapters/approvals/schema.py`,
`governance/modules/schema.py`) is a small stdlib validator covering exactly
the keywords the shipped schemas use. This module follows that precedent —
same idea, same shape, a larger keyword set because the document families
need it — rather than forking a third-party library into the tree.

Supported keywords
------------------

``$ref`` (local ``#/$defs/<name>`` JSON pointers **and** cross-file
``<file>.json#/$defs/<name>``), ``$defs`` / ``definitions``, ``type`` (string
or list), ``enum``, ``const``, ``required``, ``properties``,
``additionalProperties`` (boolean or schema), ``items``, ``minItems``,
``maxItems``, ``uniqueItems``, ``minLength``, ``maxLength``, ``pattern``,
``format`` (``date`` and ``email``), ``minimum``, ``maximum``,
``exclusiveMinimum``, ``exclusiveMaximum``, ``multipleOf``, ``allOf``,
``anyOf``, ``oneOf``, ``not``, and ``if``/``then``/``else``.

Anything else in a schema document is an annotation and is ignored, exactly as
JSON Schema requires — so a ``title``, ``description`` or the ``x-erp-*``
provenance keywords the document families carry cannot change a verdict.

**Which formats are asserted, and why only those.** ``format`` is an annotation
by default in JSON Schema, and an implementation asserts only the names it
knows. Measured against the reference implementation (``jsonschema`` 4.26 with
its own format checker enabled): it asserts ``date`` and ``email``, and does
**not** assert ``date-time`` (the name is absent from its checker set, so an
ill-formed date-time is accepted there). This validator therefore asserts
exactly ``date`` and ``email`` and treats every other format name as an
annotation, so that "this validator accepted it" and "the reference accepted
it" mean the same thing. The set is asserted by the suite, so it cannot drift.
The shipped schemas use only ``date`` and ``email``.

Two failure modes are deliberately distinct
-------------------------------------------

* an **uninterpretable schema** (an unresolvable ``$ref``, an unknown
  ``type``, recursion past :data:`MAX_DEPTH`) raises :class:`SchemaError`. A
  schema the validator cannot read must never be reported as "valid" — that
  would be the false green this repository's doctrine forbids.
* an **invalid instance** returns violation strings, each naming a
  JSON-pointer-ish location. An empty list means valid.

Integration
-----------

The test suite cross-checks this validator against the third-party
``jsonschema`` library over a corpus of valid and invalid documents (it is
installed in the gate environment; the repo's own ``check-yaml.py`` already
hard-depends on a third-party parser). If the two disagree — in either
direction — the suite fails. That is what keeps a hand-written subset from
silently drifting away from the standard it claims to implement.

---knowledge---
module_id: integrations.erp.core.schema
system: integrations
app: erp
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [SchemaError, Validator, validate, is_valid]
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
from typing import Any, Dict, List, Optional, Tuple

#: Recursion ceiling. A schema that needs more than this is either cyclic
#: without consuming its instance, or hostile; either way it is not a valid
#: verdict, so it is refused rather than guessed at.
MAX_DEPTH = 64

#: The format names this validator asserts. Pinned by the suite and measured
#: against the reference implementation, so widening the set is a deliberate act
#: rather than a quiet divergence.
ASSERTED_FORMATS = ("date", "email")

_TYPE_MAP: Dict[str, type] = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "null": type(None),
}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SchemaError(Exception):
    """A schema that cannot be interpreted (never a verdict about an instance)."""


def _is_json_type(value: Any, name: str) -> bool:
    """JSON-Schema type membership.

    ``bool`` is a Python subclass of ``int``, so a bare ``isinstance`` check
    would accept ``true`` as an ``integer`` — the classic false-accept.
    """
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if name not in _TYPE_MAP:
        raise SchemaError(f"unknown JSON Schema type {name!r}")
    expected = _TYPE_MAP[name]
    if expected is bool:
        return isinstance(value, bool)
    return isinstance(value, expected)


def _canonical(value: Any) -> str:
    """A stable rendering of a value, for ``uniqueItems`` comparison."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _check_format(value: str, fmt: str) -> bool:
    """Assert the formats this validator claims (see ASSERTED_FORMATS).

    Any other format name is an annotation and never a verdict: that is what
    keeps this validator's verdict identical to the reference implementation's,
    which asserts the same two names and no others.
    """
    if fmt not in ASSERTED_FORMATS:
        return True
    if fmt == "date":
        if not _DATE_RE.match(value):
            return False
        try:
            year, month, day = (int(part) for part in value.split("-"))
            import datetime as _dt

            _dt.date(year, month, day)
        except ValueError:
            return False
        return True
    return bool(_EMAIL_RE.match(value))


class Validator:
    """Validates instances against a JSON Schema document.

    ``base_dir`` is the directory cross-file ``$ref`` values resolve against —
    normally the directory of the referring schema. Loaded schema documents are
    cached per validator, so a document family that refs the shared envelope
    twelve times reads it once.
    """

    def __init__(self, base_dir: Optional[Path | str] = None) -> None:
        self.base_dir = Path(base_dir) if base_dir is not None else Path(".")
        self._cache: Dict[str, Dict[str, Any]] = {}

    # --- loading ----------------------------------------------------------

    def load(self, path: Path | str) -> Dict[str, Any]:
        """Load and cache one schema document."""
        resolved = Path(path)
        if not resolved.is_absolute():
            resolved = (self.base_dir / resolved).resolve()
        key = str(resolved)
        if key not in self._cache:
            if not resolved.is_file():
                raise SchemaError(f"schema document not found: {resolved}")
            with open(resolved, encoding="utf-8") as handle:
                document = json.load(handle)
            if not isinstance(document, dict):
                raise SchemaError(f"schema document must be a JSON object: {resolved}")
            self._cache[key] = document
        return self._cache[key]

    # --- reference resolution --------------------------------------------

    def _resolve(
        self, ref: str, root: Dict[str, Any], base_dir: Path
    ) -> Tuple[Any, Path, Dict[str, Any]]:
        if not isinstance(ref, str) or not ref:
            raise SchemaError(f"$ref must be a non-empty string, got {ref!r}")
        file_part, _, fragment = ref.partition("#")
        if file_part:
            target = Path(file_part)
            if not target.is_absolute():
                target = base_dir / target
            document = self.load(target)
            new_base = target.parent
            new_root = document
        else:
            document = root
            new_base = base_dir
            new_root = root
        if not fragment:
            return document, new_base, new_root
        if not fragment.startswith("/"):
            raise SchemaError(
                f"unsupported $ref fragment {fragment!r} (JSON pointers only)"
            )
        node: Any = document
        for token in fragment.lstrip("/").split("/"):
            token = token.replace("~1", "/").replace("~0", "~")
            if isinstance(node, dict):
                if token not in node:
                    raise SchemaError(f"$ref {ref!r} does not resolve")
                node = node[token]
            elif isinstance(node, list):
                try:
                    node = node[int(token)]
                except (ValueError, IndexError) as exc:
                    raise SchemaError(f"$ref {ref!r} does not resolve: {exc}") from exc
            else:
                raise SchemaError(f"$ref {ref!r} does not resolve")
        return node, new_base, new_root

    # --- the verdict ------------------------------------------------------

    def resolve(
        self,
        node: Any,
        root: Dict[str, Any],
        base_dir: Optional[Path] = None,
        *,
        depth: int = 0,
    ) -> Any:
        """Follow a chain of ``$ref``s and return the subschema it lands on.

        Used by callers that must inspect a schema *structurally* rather than
        validate against it — the asset gate reads a family's pinned ``state``
        enum, which its schema holds behind a ``document.schema.json`` reference.
        Reading the raw property there would see a ``$ref`` and conclude the enum
        is unpinned.
        """
        if depth > MAX_DEPTH:
            raise SchemaError("$ref chain exceeded MAX_DEPTH")
        if isinstance(node, dict) and "$ref" in node:
            target, new_base, new_root = self._resolve(
                node["$ref"], root, Path(base_dir) if base_dir else self.base_dir
            )
            return self.resolve(target, new_root, new_base, depth=depth + 1)
        return node

    def violations(
        self,
        instance: Any,
        schema: Any,
        *,
        where: str = "$",
        base_dir: Optional[Path] = None,
        root: Optional[Dict[str, Any]] = None,
        depth: int = 0,
    ) -> List[str]:
        """Return every way ``instance`` violates ``schema``; empty means valid."""
        if depth > MAX_DEPTH:
            raise SchemaError(f"schema recursion exceeded MAX_DEPTH at {where}")
        if schema is True:
            return []
        if schema is False:
            return [f"{where}: schema is false, so nothing validates"]
        if not isinstance(schema, dict):
            raise SchemaError(f"subschema at {where} must be an object or boolean")

        current_base = Path(base_dir) if base_dir is not None else self.base_dir
        current_root = root if root is not None else schema

        problems: List[str] = []

        if "$ref" in schema:
            target, new_base, new_root = self._resolve(
                schema["$ref"], current_root, current_base
            )
            problems.extend(
                self.violations(
                    instance,
                    target,
                    where=where,
                    base_dir=new_base,
                    root=new_root,
                    depth=depth + 1,
                )
            )

        declared = schema.get("type")
        if declared is not None:
            names = [declared] if isinstance(declared, str) else list(declared)
            if not any(_is_json_type(instance, name) for name in names):
                joined = ", ".join(sorted(names))
                return [
                    f"{where}: expected type {joined}, got {type(instance).__name__}"
                ]

        if "enum" in schema:
            allowed = schema["enum"]
            if not any(_canonical(instance) == _canonical(entry) for entry in allowed):
                rendered = ", ".join(repr(entry) for entry in allowed)
                problems.append(f"{where}: {instance!r} is not one of {rendered}")

        if "const" in schema:
            if _canonical(instance) != _canonical(schema["const"]):
                problems.append(
                    f"{where}: {instance!r} is not the required constant "
                    f"{schema['const']!r}"
                )

        if isinstance(instance, dict):
            problems.extend(
                self._violations_object(
                    instance, schema, where, current_base, current_root, depth
                )
            )
        elif isinstance(instance, list):
            problems.extend(
                self._violations_array(
                    instance, schema, where, current_base, current_root, depth
                )
            )
        elif isinstance(instance, str):
            problems.extend(self._violations_string(instance, schema, where))
        elif isinstance(instance, (int, float)) and not isinstance(instance, bool):
            problems.extend(self._violations_number(instance, schema, where))

        problems.extend(
            self._violations_combinators(
                instance, schema, where, current_base, current_root, depth
            )
        )
        return problems

    # --- applicators ------------------------------------------------------

    def _violations_object(
        self,
        instance: Dict[str, Any],
        schema: Dict[str, Any],
        where: str,
        base_dir: Path,
        root: Dict[str, Any],
        depth: int,
    ) -> List[str]:
        problems: List[str] = []
        for field in schema.get("required", []):
            if field not in instance:
                problems.append(f"{where}: missing required field '{field}'")
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for field, value in instance.items():
            if field in properties:
                problems.extend(
                    self.violations(
                        value,
                        properties[field],
                        where=f"{where}.{field}",
                        base_dir=base_dir,
                        root=root,
                        depth=depth + 1,
                    )
                )
            elif additional is False:
                problems.append(f"{where}: unknown field '{field}'")
            elif isinstance(additional, dict):
                problems.extend(
                    self.violations(
                        value,
                        additional,
                        where=f"{where}.{field}",
                        base_dir=base_dir,
                        root=root,
                        depth=depth + 1,
                    )
                )
        return problems

    def _violations_array(
        self,
        instance: List[Any],
        schema: Dict[str, Any],
        where: str,
        base_dir: Path,
        root: Dict[str, Any],
        depth: int,
    ) -> List[str]:
        problems: List[str] = []
        minimum = schema.get("minItems")
        if minimum is not None and len(instance) < minimum:
            problems.append(f"{where}: must have at least {minimum} item(s)")
        maximum = schema.get("maxItems")
        if maximum is not None and len(instance) > maximum:
            problems.append(f"{where}: must have at most {maximum} item(s)")
        if schema.get("uniqueItems") is True:
            seen: Dict[str, int] = {}
            for position, entry in enumerate(instance):
                key = _canonical(entry)
                if key in seen:
                    problems.append(
                        f"{where}: items {seen[key]} and {position} are duplicates"
                    )
                else:
                    seen[key] = position
        items = schema.get("items")
        if items is not None:
            for position, entry in enumerate(instance):
                problems.extend(
                    self.violations(
                        entry,
                        items,
                        where=f"{where}[{position}]",
                        base_dir=base_dir,
                        root=root,
                        depth=depth + 1,
                    )
                )
        return problems

    def _violations_string(
        self, instance: str, schema: Dict[str, Any], where: str
    ) -> List[str]:
        problems: List[str] = []
        minimum = schema.get("minLength")
        if minimum is not None and len(instance) < minimum:
            problems.append(f"{where}: must be at least {minimum} character(s)")
        maximum = schema.get("maxLength")
        if maximum is not None and len(instance) > maximum:
            problems.append(f"{where}: must be at most {maximum} character(s)")
        pattern = schema.get("pattern")
        if pattern is not None and not re.search(pattern, instance):
            problems.append(f"{where}: {instance!r} does not match {pattern!r}")
        fmt = schema.get("format")
        if fmt is not None and not _check_format(instance, fmt):
            problems.append(f"{where}: {instance!r} is not a valid {fmt}")
        return problems

    def _violations_number(
        self, instance: float, schema: Dict[str, Any], where: str
    ) -> List[str]:
        problems: List[str] = []
        minimum = schema.get("minimum")
        if minimum is not None and instance < minimum:
            problems.append(f"{where}: must be >= {minimum}")
        maximum = schema.get("maximum")
        if maximum is not None and instance > maximum:
            problems.append(f"{where}: must be <= {maximum}")
        exclusive_minimum = schema.get("exclusiveMinimum")
        if exclusive_minimum is not None and instance <= exclusive_minimum:
            problems.append(f"{where}: must be > {exclusive_minimum}")
        exclusive_maximum = schema.get("exclusiveMaximum")
        if exclusive_maximum is not None and instance >= exclusive_maximum:
            problems.append(f"{where}: must be < {exclusive_maximum}")
        multiple = schema.get("multipleOf")
        if multiple is not None:
            ratio = instance / multiple
            if abs(ratio - round(ratio)) > 1e-9:
                problems.append(f"{where}: must be a multiple of {multiple}")
        return problems

    def _violations_combinators(
        self,
        instance: Any,
        schema: Dict[str, Any],
        where: str,
        base_dir: Path,
        root: Dict[str, Any],
        depth: int,
    ) -> List[str]:
        problems: List[str] = []

        def matches(subschema: Any) -> bool:
            return not self.violations(
                instance,
                subschema,
                where=where,
                base_dir=base_dir,
                root=root,
                depth=depth + 1,
            )

        for index, subschema in enumerate(schema.get("allOf", [])):
            problems.extend(
                self.violations(
                    instance,
                    subschema,
                    where=f"{where} (allOf[{index}])",
                    base_dir=base_dir,
                    root=root,
                    depth=depth + 1,
                )
            )
        if "anyOf" in schema:
            if not any(matches(subschema) for subschema in schema["anyOf"]):
                problems.append(f"{where}: matches none of the anyOf branches")
        if "oneOf" in schema:
            hits = sum(1 for subschema in schema["oneOf"] if matches(subschema))
            if hits != 1:
                problems.append(
                    f"{where}: must match exactly one oneOf branch (matched {hits})"
                )
        if "not" in schema:
            if matches(schema["not"]):
                problems.append(f"{where}: matches a 'not' branch, which is forbidden")
        if "if" in schema:
            branch = "then" if matches(schema["if"]) else "else"
            if branch in schema:
                problems.extend(
                    self.violations(
                        instance,
                        schema[branch],
                        where=f"{where} ({branch})",
                        base_dir=base_dir,
                        root=root,
                        depth=depth + 1,
                    )
                )
        return problems


def validate(
    instance: Any,
    schema: Dict[str, Any],
    *,
    base_dir: Optional[Path | str] = None,
    where: str = "$",
) -> List[str]:
    """Convenience wrapper: validate ``instance`` against ``schema``."""
    return Validator(base_dir=base_dir).violations(instance, schema, where=where)


def is_valid(
    instance: Any, schema: Dict[str, Any], *, base_dir: Optional[Path | str] = None
) -> bool:
    """True when ``schema`` accepts ``instance``."""
    return not validate(instance, schema, base_dir=base_dir)
