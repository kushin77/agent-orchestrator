"""The schema engine itself must fail closed (issue #150).

If the engine silently ignored a JSON-Schema keyword it does not implement, an
unimplemented constraint would become a false green — the failure mode this
repo's no-false-green doctrine forbids. These tests attack the engine, not the
matrix.
"""

from __future__ import annotations

import json

import pytest

import model


def test_supported_keyword_set_is_the_documented_one() -> None:
    assert "type" in model.SUPPORTED_SCHEMA_KEYWORDS
    assert "required" in model.SUPPORTED_SCHEMA_KEYWORDS
    assert "additionalProperties" in model.SUPPORTED_SCHEMA_KEYWORDS


def test_shipped_schema_uses_only_implemented_keywords(package_dir) -> None:
    schema = model.read_schema(package_dir / "schema.json")
    model.assert_schema_supported(schema)
    assert schema["$schema"].startswith("http://json-schema.org/draft-07/schema#")
    assert schema["type"] == "object"


def test_unsupported_keyword_is_a_schema_error_not_a_pass() -> None:
    with pytest.raises(model.SchemaError) as caught:
        model.assert_schema_supported({"type": "object", "oneOf": [{"type": "string"}]})
    assert "oneOf" in str(caught.value)


def test_unsupported_keyword_nested_in_properties_is_also_a_schema_error() -> None:
    schema = {
        "type": "object",
        "properties": {"id": {"type": "string", "if": {"type": "integer"}}},
    }
    with pytest.raises(model.SchemaError) as caught:
        model.assert_schema_supported(schema)
    assert "if" in str(caught.value)


def test_tuple_validation_is_refused_rather_than_ignored() -> None:
    with pytest.raises(model.SchemaError):
        model.assert_schema_supported({"type": "array", "items": [{"type": "string"}]})


def test_broken_local_ref_is_a_schema_error() -> None:
    with pytest.raises(model.SchemaError):
        model.validate_instance({}, {"$ref": "#/definitions/missing", "definitions": {}})


def test_external_ref_is_refused() -> None:
    with pytest.raises(model.SchemaError):
        model.validate_instance({}, {"$ref": "https://example.invalid/schema.json"})


def test_missing_schema_file_is_a_schema_error(tmp_path) -> None:
    with pytest.raises(model.SchemaError):
        model.read_schema(tmp_path / "absent.json")


def test_non_object_schema_root_is_a_schema_error(tmp_path) -> None:
    path = tmp_path / "schema.json"
    path.write_text("[1, 2, 3]\n", encoding="utf-8")
    with pytest.raises(model.SchemaError):
        model.read_schema(path)


def test_type_required_enum_and_pattern_are_enforced() -> None:
    schema = {
        "type": "object",
        "required": ["id", "kind"],
        "additionalProperties": False,
        "properties": {
            "id": {"type": "string", "pattern": "^[a-z][a-z0-9-]*$"},
            "kind": {"enum": ["repo-fleet", "enterprise-controller"]},
            "count": {"type": "integer", "minimum": 1},
        },
    }
    assert model.validate_instance({"id": "ao-fleet", "kind": "repo-fleet"}, schema) == []
    findings = model.validate_instance({"id": "AO", "kind": "freelancer", "count": 0, "extra": True}, schema)
    joined = " | ".join(findings)
    assert "pattern" in joined
    assert "is not one of" in joined
    assert "below minimum" in joined
    assert "unexpected property" in joined
    assert any("missing required property 'id'" in one for one in model.validate_instance({"kind": "repo-fleet"}, schema))


def test_booleans_are_not_integers() -> None:
    findings = model.validate_instance({"n": True}, {"type": "object", "properties": {"n": {"type": "integer"}}})
    assert findings and "expected type integer" in findings[0]


def test_array_constraints_are_enforced() -> None:
    schema = {
        "type": "object",
        "properties": {
            "scope": {"type": "array", "minItems": 1, "maxItems": 2, "uniqueItems": True, "items": {"type": "string"}}
        },
    }
    assert model.validate_instance({"scope": ["a", "b"]}, schema) == []
    assert any("minItems" in one for one in model.validate_instance({"scope": []}, schema))
    assert any("maxItems" in one for one in model.validate_instance({"scope": ["a", "b", "c"]}, schema))
    assert any("not unique" in one for one in model.validate_instance({"scope": ["a", "a"]}, schema))
    assert any("expected type string" in one for one in model.validate_instance({"scope": [1]}, schema))


def test_ref_and_all_of_are_resolved() -> None:
    schema = {
        "type": "object",
        "definitions": {"name": {"type": "string", "minLength": 2}},
        "properties": {"name": {"$ref": "#/definitions/name"}},
        "allOf": [{"type": "object"}],
    }
    assert model.validate_instance({"name": "ao"}, schema) == []
    assert model.validate_instance({"name": "a"}, schema)


def test_shipped_matrix_satisfies_the_shipped_schema(matrix: model.Matrix) -> None:
    schema = model.read_schema()
    assert model.validate_instance(matrix.document, schema) == []


def test_schema_json_is_loadable_json(package_dir) -> None:
    payload = json.loads((package_dir / "schema.json").read_text(encoding="utf-8"))
    assert payload["$id"] == "urn:agent-orchestrator:governance:authority-matrix:v1"
