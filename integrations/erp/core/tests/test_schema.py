"""The subset validator: every keyword both ways, and against the reference.

Two things are proven here. First, each keyword the shipped schemas use has an
accept path **and** a refuse path asserted on a tiny schema that isolates it, so
a keyword that silently stopped enforcing cannot pass. Second, the whole corpus
(valid documents and mutants) is judged identically by this validator and by the
third-party ``jsonschema`` implementation — so the hand-written subset cannot
drift from the standard it claims to implement, in either direction.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from integrations.erp.core.schema import (
    ASSERTED_FORMATS,
    MAX_DEPTH,
    SchemaError,
    Validator,
    is_valid,
)

from . import documents
from .conftest import SCHEMAS

# --- keyword coverage -------------------------------------------------------

#: (label, schema, an instance it accepts, an instance it refuses)
KEYWORD_CASES = [
    ("type", {"type": "string"}, "x", 1),
    ("type-list", {"type": ["string", "null"]}, None, 1),
    ("integer-rejects-bool", {"type": "integer"}, 3, True),
    ("number-rejects-bool", {"type": "number"}, 3.5, False),
    ("enum", {"enum": ["a", "b"]}, "a", "c"),
    ("const", {"const": "fixed"}, "fixed", "other"),
    ("required", {"type": "object", "required": ["a"]}, {"a": 1}, {"b": 1}),
    (
        "additionalProperties",
        {"type": "object", "properties": {"a": {}}, "additionalProperties": False},
        {"a": 1},
        {"b": 1},
    ),
    (
        "additionalProperties-as-schema",
        {"type": "object", "additionalProperties": {"type": "integer"}},
        {"a": 1},
        {"a": "one"},
    ),
    (
        "items",
        {"type": "array", "items": {"type": "integer"}},
        [1, 2],
        [1, "two"],
    ),
    ("minItems", {"type": "array", "minItems": 2}, [1, 2], [1]),
    ("maxItems", {"type": "array", "maxItems": 2}, [1, 2], [1, 2, 3]),
    (
        "uniqueItems",
        {"type": "array", "uniqueItems": True},
        [{"a": 1}, {"a": 2}],
        [{"a": 1}, {"a": 1}],
    ),
    ("minLength", {"type": "string", "minLength": 2}, "ab", "a"),
    ("maxLength", {"type": "string", "maxLength": 2}, "ab", "abc"),
    ("pattern", {"type": "string", "pattern": "^[A-Z]{3}$"}, "USD", "usd"),
    ("format-date", {"type": "string", "format": "date"}, "2026-09-15", "15-09-2026"),
    (
        "format-date-impossible",
        {"type": "string", "format": "date"},
        "2026-02-28",
        "2026-02-30",
    ),
    ("format-email", {"type": "string", "format": "email"}, "a@b.co", "not-an-address"),
    ("minimum", {"type": "number", "minimum": 0}, 0, -1),
    ("maximum", {"type": "number", "maximum": 0}, 0, 1),
    ("exclusiveMinimum", {"type": "number", "exclusiveMinimum": 0}, 0.5, 0),
    ("exclusiveMaximum", {"type": "number", "exclusiveMaximum": 0}, -0.5, 0),
    ("multipleOf", {"type": "number", "multipleOf": 0.5}, 1.5, 1.7),
    (
        "allOf",
        {"allOf": [{"type": "integer"}, {"minimum": 2}]},
        2,
        1,
    ),
    (
        "anyOf",
        {"anyOf": [{"type": "string"}, {"type": "integer"}]},
        "x",
        1.5,
    ),
    (
        "oneOf-both",
        {"oneOf": [{"type": "integer"}, {"type": "number"}]},
        1.5,
        2,
    ),
    ("not", {"not": {"type": "string"}}, 1, "x"),
    (
        "if-then",
        {
            "type": "object",
            "if": {"properties": {"kind": {"const": "a"}}, "required": ["kind"]},
            "then": {"required": ["detail"]},
        },
        {"kind": "a", "detail": "x"},
        {"kind": "a"},
    ),
    (
        "if-else",
        {
            "type": "object",
            "if": {"properties": {"kind": {"const": "a"}}, "required": ["kind"]},
            "else": {"required": ["other"]},
        },
        {"kind": "b", "other": "x"},
        {"kind": "b"},
    ),
    (
        "ref-local",
        {
            "$defs": {"positive": {"type": "number", "exclusiveMinimum": 0}},
            "type": "object",
            "properties": {"qty": {"$ref": "#/$defs/positive"}},
        },
        {"qty": 1},
        {"qty": 0},
    ),
    (
        "ref-definitions-alias",
        {
            "definitions": {"code": {"type": "string", "pattern": "^[A-Z]+$"}},
            "type": "object",
            "properties": {"code": {"$ref": "#/definitions/code"}},
        },
        {"code": "AB"},
        {"code": "ab"},
    ),
]


@pytest.mark.parametrize(
    "schema,accepted,refused",
    [case[1:] for case in KEYWORD_CASES],
    ids=[case[0] for case in KEYWORD_CASES],
)
def test_each_keyword_accepts_and_refuses(
    validator: Validator, schema: Dict[str, Any], accepted: Any, refused: Any
) -> None:
    """Every keyword in the table has both a pass and a refuse path.

    A keyword that is deliberately an annotation is not in this table at all —
    it is covered by the annotation tests below — so nothing here can be excused
    by passing ``None`` and accidentally asserting a type mismatch instead.
    """
    assert validator.violations(accepted, schema) == []
    assert validator.violations(refused, schema) != []


def test_the_corpus_of_keywords_is_not_empty() -> None:
    assert len(KEYWORD_CASES) >= 30


def test_the_asserted_format_set_is_pinned(validator: Validator) -> None:
    """Only the formats the reference implementation asserts may be asserted.

    ``date-time`` is deliberately NOT asserted: measured against ``jsonschema``
    4.26 with its format checker enabled, that name is absent from the checker
    set, so the reference accepts an ill-formed date-time. Asserting it here
    would make the two implementations disagree, and a validator that says
    something the reference does not is the drift this cross-check exists to
    catch.
    """
    assert ASSERTED_FORMATS == ("date", "email")
    assert "date-time" not in ASSERTED_FORMATS
    permissive = {"type": "string", "format": "date-time"}
    assert validator.violations("2026-09-15 10:00:00", permissive) == []


def test_violations_name_the_location(validator: Validator) -> None:
    schema = {
        "type": "object",
        "properties": {"lines": {"type": "array", "items": {"type": "object",
                                                            "required": ["qty"]}}},
    }
    problems = validator.violations({"lines": [{}, {}]}, schema)
    assert any(problem.startswith("$.lines[0]:") for problem in problems)
    assert any(problem.startswith("$.lines[1]:") for problem in problems)


def test_cross_file_ref_resolves(validator: Validator) -> None:
    """A family's cross-file reference reads the shared document schema."""
    schema = {
        "type": "object",
        "properties": {"amount": {"$ref": "document.schema.json#/$defs/money"}},
    }
    assert validator.violations({"amount": 10}, schema) == []
    assert validator.violations({"amount": -1}, schema) != []


# --- uninterpretable schemas are never a verdict ----------------------------


def test_unresolvable_ref_is_a_schema_error(validator: Validator) -> None:
    schema = {"type": "object", "properties": {"a": {"$ref": "#/$defs/nope"}}}
    with pytest.raises(SchemaError):
        validator.violations({"a": 1}, schema)


def test_unknown_type_is_a_schema_error(validator: Validator) -> None:
    with pytest.raises(SchemaError):
        validator.violations("x", {"type": "text"})


def test_missing_schema_file_is_a_schema_error(validator: Validator) -> None:
    with pytest.raises(SchemaError):
        validator.load("no-such-schema.json")


def test_annotation_keywords_do_not_change_a_verdict(validator: Validator) -> None:
    """title/description/x-* are annotations: they cannot make a document valid."""
    plain = {"type": "integer"}
    annotated = {
        "type": "integer",
        "title": "N",
        "description": "a number",
        "x-erp-provenance": {"issue": "x"},
        "$comment": "ignored",
    }
    for instance in (1, "x", None):
        assert bool(validator.violations(instance, plain)) == bool(
            validator.violations(instance, annotated)
        )


def test_recursion_ceiling_refuses_rather_than_loops(validator: Validator) -> None:
    schema: Dict[str, Any] = {"$defs": {}, "allOf": []}
    schema["$defs"]["loop"] = {"$ref": "#/$defs/loop"}
    schema["allOf"].append({"$ref": "#/$defs/loop"})
    with pytest.raises(SchemaError):
        validator.violations({}, schema)
    assert MAX_DEPTH > 0


# --- the reference implementation ------------------------------------------


def _reference_validator(schema: Dict[str, Any]):
    """A ``jsonschema`` validator whose registry resolves our relative refs."""
    jsonschema = pytest.importorskip("jsonschema")
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012

    def retrieve(uri: str):
        name = uri.rsplit("/", 1)[-1]
        path = Path(SCHEMAS) / name
        if not path.is_file():
            raise FileNotFoundError(uri)
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
        return Resource.from_contents(document, default_specification=DRAFT202012)

    return jsonschema.Draft202012Validator(
        schema,
        registry=Registry(retrieve=retrieve),
        format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER,
    )


def _corpus() -> list:
    cases = [(kind, document) for kind, document in documents.valid_documents().items()]
    cases += [(case["kind"], documents.mutated(case)) for case in documents.mutants()]
    return cases


def test_the_corpus_has_both_halves() -> None:
    kinds = {kind for kind, _ in _corpus()}
    assert len(documents.valid_documents()) == 10
    assert len(documents.mutants()) >= 25
    assert len(kinds) == 10


@pytest.mark.parametrize("kind,document", _corpus(), ids=lambda value: str(value)[:28])
def test_the_validator_agrees_with_the_reference_implementation(
    validator: Validator, model, kind: str, document: Dict[str, Any]
) -> None:
    """My verdict and the reference verdict must agree, in both directions."""
    schema = model.schema_for(kind)
    mine = not validator.violations(document, schema)
    reference = _reference_validator(schema)
    theirs = not list(reference.iter_errors(document))
    assert mine == theirs, (
        f"{kind}: this validator says {mine}, jsonschema says {theirs}"
    )


def test_is_valid_wrapper_agrees_with_violations(validator: Validator) -> None:
    schema = {"type": "object", "required": ["a"]}
    assert is_valid({"a": 1}, schema) is True
    assert is_valid({}, schema) is False
