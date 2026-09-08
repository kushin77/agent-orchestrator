"""Tests for the bundled JSON-Schema subset validator and the shipped schemas.

No-false-green (AO-GR-4): every supported keyword genuinely rejects a
document that violates it — each negative below must raise
:class:`SchemaValidationError`.
"""

from __future__ import annotations

import pytest

from policy import SchemaValidationError
from policy.loader import _schema as _policy_schema
from policy.schemas import validate

# ---------------------------------------------------------------------------
# policy.schema.json behaviour
# ---------------------------------------------------------------------------


def _valid_policy() -> dict:
    return {
        "id": "sample-policy",
        "rules": [
            {
                "id": "block-something",
                "actions": ["model.call"],
                "decision": "block",
                "reason": "blocked for tests",
            }
        ],
    }


def test_valid_policy_document_passes_shipped_schema():
    validate(_valid_policy(), _policy_schema())  # must not raise


def test_missing_required_id_is_rejected():
    doc = _valid_policy()
    del doc["id"]
    with pytest.raises(SchemaValidationError):
        validate(doc, _policy_schema())


def test_bad_id_pattern_is_rejected():
    doc = _valid_policy()
    doc["id"] = "Bad_Id"
    with pytest.raises(SchemaValidationError) as exc:
        validate(doc, _policy_schema())
    assert any("pattern" in error for error in exc.value.errors)


def test_unknown_decision_enum_value_is_rejected():
    doc = _valid_policy()
    doc["rules"][0]["decision"] = "explode"
    with pytest.raises(SchemaValidationError):
        validate(doc, _policy_schema())


def test_rule_missing_required_actions_is_rejected():
    doc = _valid_policy()
    del doc["rules"][0]["actions"]
    with pytest.raises(SchemaValidationError):
        validate(doc, _policy_schema())


def test_empty_rules_list_violates_min_items():
    doc = _valid_policy()
    doc["rules"] = []
    with pytest.raises(SchemaValidationError):
        validate(doc, _policy_schema())


def test_extra_top_level_property_is_rejected():
    doc = _valid_policy()
    doc["mystery"] = True
    with pytest.raises(SchemaValidationError) as exc:
        validate(doc, _policy_schema())
    assert any("mystery" in error for error in exc.value.errors)


def test_duplicate_control_ids_violate_unique_items():
    doc = _valid_policy()
    doc["controls"] = ["model-call-budget", "model-call-budget"]
    with pytest.raises(SchemaValidationError) as exc:
        validate(doc, _policy_schema())
    assert any("unique" in error for error in exc.value.errors)


def test_non_string_action_is_rejected():
    doc = _valid_policy()
    doc["rules"][0]["actions"] = [42]
    with pytest.raises(SchemaValidationError):
        validate(doc, _policy_schema())


def test_rule_id_pattern_and_reason_length_are_enforced():
    doc = _valid_policy()
    doc["rules"][0]["id"] = "Block_Shell"
    with pytest.raises(SchemaValidationError):
        validate(doc, _policy_schema())

    doc = _valid_policy()
    doc["rules"][0]["decision"] = "log"
    doc["rules"][0]["reason"] = ""
    with pytest.raises(SchemaValidationError):
        validate(doc, _policy_schema())


# ---------------------------------------------------------------------------
# generic validator keyword coverage
# ---------------------------------------------------------------------------


def test_type_list_form_and_minimum_maximum():
    schema = {"type": ["string", "null"]}
    validate("ok", schema)
    validate(None, schema)
    with pytest.raises(SchemaValidationError):
        validate(42, schema)

    numeric = {"type": "integer", "minimum": 1, "maximum": 10}
    validate(5, numeric)
    with pytest.raises(SchemaValidationError):
        validate(0, numeric)
    with pytest.raises(SchemaValidationError):
        validate(11, numeric)


def test_boolean_and_integer_are_distinct():
    schema = {"type": "integer"}
    with pytest.raises(SchemaValidationError):
        validate(True, schema)


def test_min_length_max_length_and_pattern_on_strings():
    schema = {"type": "string", "minLength": 2, "maxLength": 4, "pattern": "^[a-z]+$"}
    validate("ab", schema)
    with pytest.raises(SchemaValidationError):
        validate("a", schema)
    with pytest.raises(SchemaValidationError):
        validate("abcde", schema)
    with pytest.raises(SchemaValidationError):
        validate("AB", schema)


def test_min_items_max_items_and_unique_items_on_arrays():
    schema = {"type": "array", "minItems": 2, "maxItems": 3, "uniqueItems": True}
    validate(["a", "b"], schema)
    validate(["a", "b", "c"], schema)
    with pytest.raises(SchemaValidationError):
        validate(["a"], schema)
    with pytest.raises(SchemaValidationError):
        validate(["a", "b", "c", "d"], schema)
    with pytest.raises(SchemaValidationError):
        validate(["a", "a"], schema)


def test_items_applies_to_every_element():
    schema = {"type": "array", "items": {"type": "string"}}
    validate(["a", "b"], schema)
    with pytest.raises(SchemaValidationError):
        validate(["a", 2], schema)


def test_const_and_enum():
    validate("block", {"enum": ["block", "warn", "log"]})
    with pytest.raises(SchemaValidationError):
        validate("allow", {"enum": ["block", "warn", "log"]})
    validate(1, {"const": 1})
    with pytest.raises(SchemaValidationError):
        validate(2, {"const": 1})


def test_local_ref_resolution_into_definitions():
    schema = {
        "type": "object",
        "properties": {"inner": {"$ref": "#/definitions/thing"}},
        "definitions": {
            "thing": {"type": "string", "minLength": 1}
        },
    }
    validate({"inner": "x"}, schema)
    with pytest.raises(SchemaValidationError):
        validate({"inner": 3}, schema)


def test_unresolvable_ref_is_reported():
    schema = {"$ref": "#/definitions/nope", "definitions": {}}
    with pytest.raises(SchemaValidationError):
        validate({"x": 1}, schema)


def test_one_of_requires_exactly_one_matching_branch():
    schema = {
        "oneOf": [
            {"type": "string"},
            {"type": "integer"},
        ]
    }
    validate("text", schema)
    validate(3, schema)
    with pytest.raises(SchemaValidationError):
        validate(True, schema)  # matches neither


def test_any_of_and_not():
    schema = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    validate("x", schema)
    validate(None, schema)
    with pytest.raises(SchemaValidationError):
        validate(9, schema)

    not_schema = {"not": {"type": "string"}}
    validate(9, not_schema)
    with pytest.raises(SchemaValidationError):
        validate("x", not_schema)
