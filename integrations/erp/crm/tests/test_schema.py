"""The stdlib schema validator and its keyword freeze (issue #650)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from integrations.erp.crm import schema
from integrations.erp.crm.model import Refused

HERE = Path(__file__).resolve().parent.parent


def test_the_shipped_schemas_use_only_implemented_keywords() -> None:
    for name in ("document", "definitions", "provenance"):
        path = HERE / "schema" / f"{name}.schema.json"
        document = schema.load(path)
        assert schema.check_schema(document, where=str(path)) == []


def test_the_shipped_schemas_parse_as_json() -> None:
    for name in ("document", "definitions", "provenance"):
        path = HERE / "schema" / f"{name}.schema.json"
        assert isinstance(json.loads(path.read_text(encoding="utf-8")), dict)


def test_an_unimplemented_keyword_is_refused_by_name() -> None:
    """The freeze is a control: an ignored keyword is a requirement nobody measures."""
    with pytest.raises(Refused, match="unsupported-schema-keyword") as caught:
        schema.assert_supported({"type": "object", "$ref": "#/$defs/thing"})
    assert "$ref" in caught.value.detail


def test_an_unimplemented_keyword_is_refused_nested_in_properties() -> None:
    problems = schema.check_schema(
        {"type": "object", "properties": {"name": {"type": "string", "format": "email"}}}
    )
    assert len(problems) == 1
    assert "format" in problems[0]


def test_the_keyword_freeze_lists_what_it_does_enforce() -> None:
    with pytest.raises(Refused, match="unsupported-schema-keyword") as caught:
        schema.assert_supported({"type": "object", "minProperties": 1})
    assert "supported:" in caught.value.detail


def test_validate_accepts_a_conforming_instance() -> None:
    instance = {"kind": "lead", "id": "LEAD-0001"}
    document = {
        "type": "object",
        "required": ["kind", "id"],
        "additionalProperties": False,
        "properties": {"kind": {"type": "string"}, "id": {"type": "string", "minLength": 3}},
    }
    assert schema.validate(instance, document) == []


def test_validate_names_a_type_violation() -> None:
    assert schema.validate({"n": "1"}, {"type": "object", "properties": {"n": {"type": "integer"}}}) == [
        "document.n: expected integer, got str"
    ]


def test_validate_does_not_accept_a_boolean_as_an_integer() -> None:
    """bool is an int subclass; a check that accepts it has stopped measuring."""
    problems = schema.validate({"n": True}, {"type": "object", "properties": {"n": {"type": "integer"}}})
    assert problems == ["document.n: expected integer, got bool"]


def test_validate_reports_missing_required_and_unknown_fields() -> None:
    problems = schema.validate(
        {"extra": 1},
        {
            "type": "object",
            "required": ["kind"],
            "additionalProperties": False,
            "properties": {"kind": {"type": "string"}},
        },
    )
    assert problems == ["document: missing required field 'kind'", "document: unknown field 'extra'"]


def test_validate_enforces_enum_minlength_pattern_minimum_and_const() -> None:
    document = {
        "type": "object",
        "properties": {
            "state": {"type": "string", "enum": ["new", "contacted"]},
            "code": {"type": "string", "minLength": 3, "pattern": "^[A-Z]+$"},
            "n": {"type": "integer", "minimum": 2, "maximum": 4},
            "policy": {"type": "string", "const": "patterns-only-no-upstream-code"},
        },
    }
    problems = schema.validate(
        {"state": "delivered", "code": "ab", "n": 9, "policy": "anything"}, document
    )
    assert any("is not one of new, contacted" in problem for problem in problems)
    assert any("at least 3 character" in problem for problem in problems)
    assert any("does not match" in problem for problem in problems)
    assert any("must be >= 2" in problem or "must be <= 4" in problem for problem in problems)
    assert any("must be 'patterns-only-no-upstream-code'" in problem for problem in problems)


def test_validate_reports_each_array_entry_positionally() -> None:
    problems = schema.validate(
        {"items": [1, "two"]},
        {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "integer"}}}},
    )
    assert problems == ["document.items[1]: expected integer, got str"]


def test_validate_named_refuses_an_unenforceable_schema_before_the_instance() -> None:
    """An instance cannot be judged by a promise nothing enforces."""
    problems = schema.validate_named(
        {"anything": True}, {"type": "object", "minProperties": 5}, "document", schema_label="scratch"
    )
    assert len(problems) == 1 and "minProperties" in problems[0]


def test_field_type_problems_rejects_a_bool_and_an_unknown_type() -> None:
    assert schema.field_type_problems(True, "integer", "x") == ["x: expected integer, got bool"]
    assert schema.field_type_problems(1, "sausage", "x") == [
        "x: unknown field type 'sausage' (known: boolean, integer, number, string)"
    ]


def test_field_type_problems_enforces_the_declared_range() -> None:
    assert schema.field_type_problems(-1, "integer", "x", minimum=0) == ["x: must be >= 0"]
    assert schema.field_type_problems(5, "integer", "x", maximum=4) == ["x: must be <= 4"]


def test_sorted_problems_is_stable_and_deduplicated() -> None:
    assert schema.sorted_problems(["b", "a", "b"]) == ["a", "b"]


def test_load_refuses_a_non_object_schema(tmp_path: Path) -> None:
    path = tmp_path / "bad.schema.json"
    path.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a JSON object"):
        schema.load(path)
