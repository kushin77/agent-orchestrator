"""Envelope parsing and per-kind field validation (issue #650).

A document enters this package as untrusted JSON and leaves as a
:class:`~.model.Document`, and the two refusals that stand between those are
separate on purpose:

* :func:`parse` judges the **envelope** against the frozen
  ``schema/document.schema.json`` — shape, required keys, id/tenant form — and
  then two facts only the definition set can answer: the kind is declared, and
  the state is one of that kind's states. An envelope that does not satisfy its
  own schema is ``schema-violation``; a state that belongs to a *different*
  kind is ``unknown-state``, not a schema problem, because the envelope shape is
  fine.
* :func:`validate_fields` judges the **kind's fields** against that kind's
  declaration: an undeclared field is ``unknown-field``, a missing required one
  is ``missing-field``, a value of the wrong declared type is ``invalid-value``,
  and a value outside its declared vocabulary is ``unknown-vocabulary-term``.

Every refusal names the offending field (and, where there is one, the
vocabulary it was read against), which is what makes a refusal actionable: the
caller is told which field to fix, not that "the document is invalid".

---knowledge---
module_id: integrations.erp.crm.documents
system: integrations
app: erp
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [parse, validate_fields, validate_range, envelope_of, known_kinds]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping

from . import schema as schemas
from .definitions import DefinitionSet
from .model import SCHEMA_VERSION, Document, Refused

DOCUMENT_SCHEMA = Path(__file__).resolve().parent / "schema" / "document.schema.json"

#: The envelope keys a caller may set. Anything else is an unknown field.
ENVELOPE_KEYS = ("schemaVersion", "kind", "id", "tenant", "state", "title", "owner", "fields")

#: Kinds whose ``state`` a caller may not choose at creation time: the machine's
#: initial state is the declaration's business, not the caller's.
_CHILD_KINDS = ("timesheet",)


def _document_schema() -> Mapping[str, Any]:
    schema = schemas.load(DOCUMENT_SCHEMA)
    schemas.assert_supported(schema, where=str(DOCUMENT_SCHEMA))
    return schema


def parse(payload: Any, definitions: DefinitionSet, *, where: str = "document") -> Document:
    """Validate an envelope and return the typed document it describes."""
    problems = schemas.validate(payload, _document_schema(), where=where)
    if problems:
        raise Refused(
            "schema-violation", "; ".join(schemas.sorted_problems(problems))
        )

    assert isinstance(payload, dict)
    kind = payload["kind"]
    declared = definitions.kind(kind)
    state = payload["state"]
    if not declared.is_state(state):
        raise Refused(
            "unknown-state",
            f"{where}: state {state!r} is not a state of a {kind} "
            f"(declared: {', '.join(declared.states)})",
        )
    document = Document(
        kind=kind,
        id=payload["id"],
        tenant=payload["tenant"],
        state=state,
        title=payload.get("title", ""),
        owner=payload.get("owner", ""),
        fields=dict(payload.get("fields", {})),
        schema_version=payload.get("schemaVersion", SCHEMA_VERSION),
    )
    validate_fields(document, definitions, where=where)
    return document


def validate_fields(
    document: Document,
    definitions: DefinitionSet,
    *,
    where: str | None = None,
) -> None:
    """Check a document's fields against its kind's declaration; raise on the first."""
    label = where or document.id
    declared = definitions.kind(document.kind)
    for name in document.fields:
        if name not in declared.allowed_fields:
            raise Refused(
                "unknown-field",
                f"{label}: field {name!r} is not allowed on a {document.kind} "
                f"(allowed: {', '.join(declared.allowed_fields)})",
            )
    for name in declared.required_fields:
        if name not in document.fields:
            raise Refused(
                "missing-field",
                f"{label}: a {document.kind} requires field {name!r}",
            )
    for name in declared.allowed_fields:
        if name not in document.fields:
            continue
        value = document.fields[name]
        expected_type = declared.field_type(name)
        if expected_type is not None:
            problems = schemas.field_type_problems(
                value, expected_type, f"{label}.{name}", minimum=0
            )
            if problems:
                raise Refused("invalid-value", "; ".join(problems))
        vocabulary_name = declared.vocabulary_for(name)
        if vocabulary_name is not None:
            terms = definitions.vocabulary(vocabulary_name)
            if not isinstance(value, str) or value not in terms:
                raise Refused(
                    "unknown-vocabulary-term",
                    f"{label}.{name}: {value!r} is not one of the "
                    f"{vocabulary_name} terms ({', '.join(terms)})",
                )


def validate_range(
    document: Document,
    definitions: DefinitionSet,
    field: str,
    *,
    minimum: int = 1,
    maximum: int | None = None,
) -> None:
    """Check one integer field is inside a range this module (not the declaration) owns."""
    declared = definitions.kind(document.kind)
    if field not in declared.allowed_fields:
        raise Refused(
            "unknown-field",
            f"{document.id}: {field!r} is not allowed on a {document.kind}",
        )
    value = document.fields.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise Refused(
            "invalid-value", f"{document.id}.{field}: expected an integer, got {value!r}"
        )
    if value < minimum:
        raise Refused(
            "invalid-value", f"{document.id}.{field}: {value} is below the minimum {minimum}"
        )
    if maximum is not None and value > maximum:
        raise Refused(
            "invalid-value", f"{document.id}.{field}: {value} is above the maximum {maximum}"
        )


def envelope_of(document: Document) -> Dict[str, Any]:
    """A document's envelope as untrusted JSON, for a round-trip through ``parse``."""
    return document.to_dict()


def known_kinds(definitions: DefinitionSet) -> List[str]:
    """Every kind the declaration set covers, sorted."""
    return sorted(definitions.kinds)
