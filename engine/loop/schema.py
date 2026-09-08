"""engine/loop.schema — output-schema validation (schema-validated outputs).

Issue #23 acceptance #1 requires *schema-validated outputs* with an explicit
parse-failure policy (``retry`` or ``CANNOT-ASSESS``).  This module ships a
small, honest, offline declarative schema validator plus a JSON-coercion
helper, mirroring the zod ``OutputSchema.parse`` discipline of the harvested
``gmail-agent`` loop (parse every model output; a malformed output is a real
failure, never a silent pass).

:class:`SchemaValidator` is deliberately tiny (stdlib only, deterministic,
no network) but genuinely capable of failing: an object missing a required
field, or a property of the wrong type, returns a non-empty error string.
Tests exercise both the accept and the reject paths.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Optional, Tuple

# Leaf type names understood by the validator.
_TYPES = ("string", "integer", "number", "boolean", "array", "object", "null", "any")

_JSON_TYPES = {"str": "string", "int": "integer", "float": "number",
               "bool": "boolean", "list": "array", "dict": "object",
               "NoneType": "null"}


def coerce_json(raw: Any) -> Tuple[Any, Optional[str]]:
    """Coerce a step output to a JSON value.

    A ``str`` payload is parsed as JSON (the gmail pattern: the model returns
    text that ``OutputSchema.parse(json.loads(text))`` validates); any other
    value is returned as-is.  Returns ``(value, None)`` on success and
    ``(None, error)`` when a string is not valid JSON — a parse failure.
    """
    if isinstance(raw, str):
        try:
            return json.loads(raw), None
        except (json.JSONDecodeError, ValueError) as exc:
            return None, f"invalid JSON: {exc}"
    return raw, None


def _type_name(value: Any) -> str:
    return _JSON_TYPES.get(type(value).__name__, "any")


class SchemaValidator:
    """A minimal declarative object-schema validator.

    ``spec`` shape (a subset of JSON Schema, kept offline and deterministic):

    .. code-block:: python

        {"type": "object",
         "properties": {"answer": {"type": "string"},
                        "count": {"type": "integer"}},
         "required": ["answer"]}

    ``properties`` values may name any leaf type in :data:`_TYPES` (``array``
    and ``object`` are validated only by container type here — this is a
    lightweight gate, not a full JSON-Schema implementation).  Validation
    returns ``None`` when the value conforms and an error string otherwise.
    """

    def __init__(self, spec: Mapping[str, Any]) -> None:
        if not isinstance(spec, dict) or spec.get("type") != "object":
            raise ValueError("schema spec must be a JSON object schema")
        self._properties = spec.get("properties", {})
        self._required = set(spec.get("required", []))
        for prop, subspec in self._properties.items():
            if not isinstance(subspec, dict) or subspec.get("type") not in _TYPES:
                raise ValueError(f"invalid type for property {prop!r}")

    def validate(self, value: Any) -> Optional[str]:
        """Return ``None`` when ``value`` conforms, else an error string."""
        if not isinstance(value, dict):
            return f"expected object, got {_type_name(value)}"
        for field in sorted(self._required):
            if field not in value:
                return f"missing required field {field!r}"
        for field, subspec in self._properties.items():
            if field not in value:
                continue
            expected = subspec["type"]
            actual = _type_name(value[field])
            if expected != "any" and actual != expected:
                return f"field {field!r}: expected {expected}, got {actual}"
        return None

    def parse(self, raw: Any) -> Tuple[Any, Optional[str]]:
        """Coerce + validate one step output.

        Returns ``(parsed_value, None)`` on success and
        ``(raw_value, error)`` when the output fails to parse/validate —
        the caller's parse-failure policy then decides between ``retry`` and
        ``CANNOT-ASSESS``.
        """
        value, coercion_error = coerce_json(raw)
        if coercion_error is not None:
            return raw, coercion_error
        error = self.validate(value)
        if error is not None:
            return raw, error
        return value, None


def validate_output(
    raw: Any,
    schema: Optional[Mapping[str, Any]],
) -> Tuple[Any, Optional[str]]:
    """Validate ``raw`` against ``schema`` when one is declared.

    With no schema declared every output is accepted (validation is opt-in,
    matching how profiles may omit a ``finalSchema``).  Returns
    ``(parsed_value, None)`` or ``(raw, error)``.
    """
    if schema is None:
        return raw, None
    return SchemaValidator(schema).parse(raw)
