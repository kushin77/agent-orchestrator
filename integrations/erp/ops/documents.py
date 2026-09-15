"""Envelope parsing: the checks that name *which* field is wrong.

ERP-02's ``DocumentModel.validate_document`` answers one question — does this
document satisfy its family schema — and answers it with a list of JSON-pointer
locations. That is the right answer for a validator and too coarse for a caller:
"missing required field 'supplier'" and "unknown field 'suplier'" are different
mistakes with different fixes, and a lane that reports both as
``schema-violation`` makes its caller read a pointer to find out which.

So this module sits in front of the model and refuses each of those by its own
name — ``missing-field``, ``unknown-field``, ``invalid-value`` — and only then
delegates to the schema for everything it cannot attribute to a single field
(``schema-violation``). The order matters: a document with a missing field is
refused as ``missing-field`` and never reaches the schema, so the code a caller
branches on is stable rather than dependent on which check happened to run
first.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Tuple

from .model import Model, Refused

__all__ = ["envelope_kind", "json_type_name", "parse", "required_fields"]


def json_type_name(value: Any) -> str:
    """The JSON Schema type name of a Python value.

    ``bool`` is a subclass of ``int``, so a bare ``isinstance`` chain would
    report ``True`` as an ``integer`` — the classic false-accept this function
    exists to avoid.
    """
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, (list, tuple)):
        return "array"
    if value is None:
        return "null"
    return type(value).__name__


def _accepts(value: Any, declared: Any) -> bool:
    names = [declared] if isinstance(declared, str) else list(declared)
    actual = json_type_name(value)
    return any(name == actual or (name == "number" and actual == "integer") for name in names)


def envelope_kind(document: Mapping[str, Any]) -> Optional[str]:
    """The ``doctype`` a document declares, when it declares a usable one."""
    kind = document.get("doctype")
    if isinstance(kind, str) and kind.strip():
        return kind
    return None


def required_fields(schema: Mapping[str, Any]) -> Tuple[str, ...]:
    """The top-level fields the family schema marks required."""
    declared = schema.get("required")
    if not isinstance(declared, list):
        return ()
    return tuple(str(name) for name in declared)


def parse(
    model: Model,
    document: Any,
    *,
    kind: Optional[str] = None,
    where: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate one document, refusing by the most specific code that applies.

    ``kind`` pins the family (used when a caller is building a document of a
    known family and a mismatched ``doctype`` is a caller bug rather than a
    lookup failure). ``where`` names the document in a refusal, so a failure
    names the offender instead of "the document".
    """
    if not isinstance(document, Mapping):
        raise Refused(
            "invalid-value",
            f"{where or '<document>'}: a document must be a mapping, got "
            f"{type(document).__name__}",
        )

    resolved = kind if kind is not None else envelope_kind(document)
    if resolved is None:
        raise Refused(
            "unknown-kind",
            f"{where or '<document>'}: the document declares no doctype, so no "
            f"family schema applies; known: {list(model.kinds())}",
        )
    declared_kind = envelope_kind(document)
    if declared_kind is not None and declared_kind != resolved:
        raise Refused(
            "invalid-value",
            f"{where or '<document>'}: doctype {declared_kind!r} is not {resolved!r}",
        )

    schema = model.schema_for(resolved)
    label = where or str(document.get("id") or f"<{resolved}>")

    missing = [name for name in required_fields(schema) if name not in document]
    if missing:
        raise Refused(
            "missing-field",
            f"{label}: a {resolved} must declare {', '.join(missing)}",
        )

    if schema.get("additionalProperties") is False:
        properties = schema.get("properties")
        known = set(properties) if isinstance(properties, Mapping) else set()
        extra = [name for name in document if name not in known]
        if extra:
            raise Refused(
                "unknown-field",
                f"{label}: a {resolved} declares no field {', '.join(sorted(extra))}; "
                f"known: {sorted(known)}",
            )

    properties = schema.get("properties")
    if isinstance(properties, Mapping):
        for name, node in properties.items():
            if name not in document or not isinstance(node, Mapping):
                continue
            declared_type = node.get("type")
            if declared_type is None or "$ref" in node:
                continue
            if not _accepts(document[name], declared_type):
                expected = (
                    declared_type if isinstance(declared_type, str) else "/".join(declared_type)
                )
                raise Refused(
                    "invalid-value",
                    f"{label}: {name} must be {expected}, got "
                    f"{json_type_name(document[name])}",
                )

    return model.validate(resolved, document)
