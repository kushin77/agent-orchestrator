"""Schema contract tests: the declared policies validate, and the validator bites.

Two claims are proven here, and a third is proven by
``test_policy_semantics.py``:

1. the shipped policies satisfy the schema (so "declared data under a validated
   schema" is a measured fact, not a claim in a README);
2. every keyword the validator implements genuinely rejects a document that
   violates it -- a keyword that cannot fail would be a formality (GR-12).
"""

from __future__ import annotations

import datetime
from typing import Any, Dict

import pytest
import yaml

import jsonschema_lite as jsl

from conftest import DEFINITIONS


def schema_error(document: Any, definition: str, schema: Dict[str, Any]) -> str:
    """Return the violation text, or "" when the document is valid."""
    try:
        jsl.validate(document, jsl.subschema(schema, definition), root=schema)
    except jsl.SchemaValidationError as exc:
        return str(exc)
    return ""


# --- the schema document itself ---------------------------------------------
def test_schema_declares_the_draft_and_the_three_definitions(schema: Dict[str, Any]) -> None:
    assert schema["$schema"] == "http://json-schema.org/draft-07/schema#"
    definitions = schema["definitions"]
    for definition in DEFINITIONS.values():
        assert definition in definitions


def test_schema_pins_the_closed_tier_vocabulary(schema: Dict[str, Any]) -> None:
    tier_name = schema["definitions"]["tier_name"]
    assert tier_name["enum"] == ["flash", "pro", "auditor"]


def test_schema_declares_updated_at_a_string(schema: Dict[str, Any]) -> None:
    # An unquoted YAML date is a datetime.date, and a date a human wrote
    # correctly must not fail a `type: string` -- the loader normalises it.
    assert schema["definitions"]["updated_at"]["type"] == "string"


# --- the shipped policies ----------------------------------------------------
@pytest.mark.parametrize("filename", sorted(DEFINITIONS))
def test_shipped_policy_satisfies_its_definition(
    filename: str, policy_documents: Dict[str, Dict[str, Any]], schema: Dict[str, Any]
) -> None:
    assert schema_error(policy_documents[filename], DEFINITIONS[filename], schema) == ""


def test_every_policy_is_a_mapping(policy_documents: Dict[str, Dict[str, Any]]) -> None:
    for document in policy_documents.values():
        assert isinstance(document, dict)


# --- negative controls: the schema rejects malformed declarations ------------
def test_dropping_a_required_key_is_rejected(schema: Dict[str, Any]) -> None:
    document = {
        "version": "1.0.0",
        "updated_at": "2026-07-24",
        "source": "x",
        "description": "x",
        # agents, worker_fleet, ... all missing
    }
    error = schema_error(document, "capability_registry", schema)
    assert "missing required property 'agents'" in error


def test_unknown_top_level_key_is_rejected(
    policy_documents: Dict[str, Dict[str, Any]], schema: Dict[str, Any]
) -> None:
    document = dict(policy_documents["tier-policy.yaml"])
    document["extra_knobs"] = True
    error = schema_error(document, "tier_policy", schema)
    assert "additional property 'extra_knobs' is not allowed" in error


def test_wrong_scalar_type_is_rejected(
    policy_documents: Dict[str, Dict[str, Any]], schema: Dict[str, Any]
) -> None:
    document = yaml.safe_load(
        yaml.safe_dump(policy_documents["route-policy.yaml"], sort_keys=False)
    )
    document["thresholds"]["fast_path_max_tokens"] = "many"
    error = schema_error(document, "route_policy", schema)
    assert "expected type ['integer']" in error


def test_non_numeric_threshold_is_rejected(
    policy_documents: Dict[str, Dict[str, Any]], schema: Dict[str, Any]
) -> None:
    document = yaml.safe_load(
        yaml.safe_dump(policy_documents["route-policy.yaml"], sort_keys=False)
    )
    document["thresholds"]["fast_path_max_tokens"] = 0
    error = schema_error(document, "route_policy", schema)
    assert "violates minimum 1" in error


def test_tier_missing_a_cost_control_is_rejected(
    policy_documents: Dict[str, Dict[str, Any]], schema: Dict[str, Any]
) -> None:
    document = yaml.safe_load(
        yaml.safe_dump(policy_documents["tier-policy.yaml"], sort_keys=False)
    )
    del document["tiers"]["flash"]["max_tokens"]
    error = schema_error(document, "tier_policy", schema)
    assert "missing required property 'max_tokens'" in error


def test_tier_fallback_must_be_a_string_or_null(
    policy_documents: Dict[str, Dict[str, Any]], schema: Dict[str, Any]
) -> None:
    document = yaml.safe_load(
        yaml.safe_dump(policy_documents["tier-policy.yaml"], sort_keys=False)
    )
    document["tiers"]["pro"]["fallback"] = 7
    error = schema_error(document, "tier_policy", schema)
    assert "expected type ['string', 'null']" in error


def test_unknown_model_tier_is_rejected(
    policy_documents: Dict[str, Dict[str, Any]], schema: Dict[str, Any]
) -> None:
    document = yaml.safe_load(
        yaml.safe_dump(policy_documents["route-policy.yaml"], sort_keys=False)
    )
    document["routes"]["fast"]["model_tier"] = "gpt-5"
    error = schema_error(document, "route_policy", schema)
    assert "not in enum" in error


def test_unknown_access_level_is_rejected(
    policy_documents: Dict[str, Dict[str, Any]], schema: Dict[str, Any]
) -> None:
    document = yaml.safe_load(
        yaml.safe_dump(policy_documents["capability-registry.yaml"], sort_keys=False)
    )
    document["worker_fleet"]["code-fleet"]["access_level"] = "root"
    error = schema_error(document, "capability_registry", schema)
    assert "not in enum" in error


def test_agent_with_no_worker_fleet_field_is_rejected(
    policy_documents: Dict[str, Dict[str, Any]], schema: Dict[str, Any]
) -> None:
    document = yaml.safe_load(
        yaml.safe_dump(policy_documents["capability-registry.yaml"], sort_keys=False)
    )
    document["agents"][0]["speed"] = "fast"
    error = schema_error(document, "capability_registry", schema)
    assert "additional property 'speed' is not allowed" in error


def test_complexity_range_key_must_look_like_a_range(
    policy_documents: Dict[str, Dict[str, Any]], schema: Dict[str, Any]
) -> None:
    document = yaml.safe_load(
        yaml.safe_dump(policy_documents["tier-policy.yaml"], sort_keys=False)
    )
    document["complexity_to_tier"]["easy"] = "flash"
    error = schema_error(document, "tier_policy", schema)
    assert "does not match pattern" in error


def test_empty_fleet_map_is_rejected(
    policy_documents: Dict[str, Dict[str, Any]], schema: Dict[str, Any]
) -> None:
    document = yaml.safe_load(
        yaml.safe_dump(policy_documents["capability-registry.yaml"], sort_keys=False)
    )
    document["worker_fleet"] = {}
    error = schema_error(document, "capability_registry", schema)
    assert "expected at least 1 propert" in error


# --- the validator's own keywords -------------------------------------------
VALIDATOR_CASES = (
    ({"type": "string"}, "x", True),
    ({"type": "string"}, 1, False),
    ({"type": "integer"}, True, False),
    ({"type": ["string", "null"]}, None, True),
    ({"type": ["string", "null"]}, 3, False),
    ({"enum": ["a", "b"]}, "c", False),
    ({"enum": ["a", "b"]}, "b", True),
    ({"const": "a"}, "b", False),
    ({"type": "object", "required": ["k"]}, {}, False),
    ({"type": "object", "required": ["k"]}, {"k": 1}, True),
    ({"type": "object", "properties": {"k": {"type": "string"}},
      "additionalProperties": False}, {"j": 1}, False),
    ({"type": "object", "minProperties": 1}, {}, False),
    ({"type": "object", "propertyNames": {"pattern": "^a"}}, {"b": 1}, False),
    ({"type": "object", "propertyNames": {"pattern": "^a"}}, {"ab": 1}, True),
    ({"type": "array", "items": {"type": "integer"}}, [1, "x"], False),
    ({"type": "array", "minItems": 1}, [], False),
    ({"type": "array", "maxItems": 1}, [1, 2], False),
    ({"type": "array", "uniqueItems": True}, [1, 1], False),
    # JSON equality: booleans are not numbers, so [true, 1] is unique.
    ({"type": "array", "uniqueItems": True}, [True, 1], True),
    ({"type": "string", "minLength": 2}, "a", False),
    ({"type": "string", "maxLength": 1}, "ab", False),
    ({"type": "string", "pattern": "^a+$"}, "b", False),
    ({"type": "integer", "minimum": 2}, 1, False),
    ({"type": "integer", "maximum": 1}, 2, False),
    ({"type": "integer", "exclusiveMinimum": 1}, 1, False),
    ({"allOf": [{"type": "string"}, {"minLength": 2}]}, "a", False),
    ({"allOf": [{"type": "string"}, {"minLength": 1}]}, "a", True),
    ({"anyOf": [{"type": "string"}, {"type": "integer"}]}, 1.5, False),
    ({"oneOf": [{"type": "number"}, {"type": "integer"}]}, 1, False),
    ({"oneOf": [{"type": "number"}, {"type": "integer"}]}, 1.5, True),
    ({"not": {"type": "string"}}, "x", False),
    ({"not": {"type": "string"}}, 1, True),
    (False, "anything", False),
    (True, "anything", True),
)


@pytest.mark.parametrize("check,instance,accepted", VALIDATOR_CASES)
def test_validator_keyword_is_not_vacuous(
    check: Dict[str, Any], instance: Any, accepted: bool
) -> None:
    if accepted:
        jsl.validate(instance, check)
    else:
        with pytest.raises(jsl.SchemaValidationError):
            jsl.validate(instance, check)


def test_ref_resolves_against_the_supplied_root() -> None:
    root = {"definitions": {"thing": {"type": "string"}}}
    jsl.validate("ok", {"$ref": "#/definitions/thing"}, root=root)
    with pytest.raises(jsl.SchemaValidationError):
        jsl.validate(3, {"$ref": "#/definitions/thing"}, root=root)


def test_unresolvable_ref_is_reported() -> None:
    with pytest.raises(jsl.SchemaValidationError, match="unresolvable"):
        jsl.validate(1, {"$ref": "#/definitions/missing"}, root={"definitions": {}})


def test_non_local_ref_is_refused() -> None:
    with pytest.raises(jsl.SchemaValidationError, match="non-local"):
        jsl.validate(1, {"$ref": "https://example.invalid/schema.json"})


def test_subschema_requires_a_declared_definition(schema: Dict[str, Any]) -> None:
    with pytest.raises(jsl.SchemaValidationError):
        jsl.subschema(schema, "not_a_definition")


def test_all_violations_are_reported_together(schema: Dict[str, Any]) -> None:
    document = {
        "version": "not-semver",
        "updated_at": 7,
        "source": "",
        "description": "",
        "agents": "not-a-list",
        "worker_fleet": {},
        "dispatch_routing": {},
        "sme_domains": {},
        "squads": {},
        "squad_default": "",
        "sme_default": "",
        "module_authority": [],
    }
    error = schema_error(document, "capability_registry", schema)
    assert error.count("- ") >= 5


# --- the PyYAML date trap ----------------------------------------------------
def test_pyyaml_would_coerce_an_unquoted_date_to_a_date_object() -> None:
    parsed = yaml.safe_load("updated_at: 2026-07-24\n")
    assert isinstance(parsed["updated_at"], datetime.date)


def test_loader_normalises_a_coerced_date_back_to_a_string(policy_variant, load_router_fixture) -> None:
    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "capability-registry.yaml":
            document["updated_at"] = datetime.date(2026, 7, 24)
        return document

    bundle = load_router_fixture(policy_variant(mutate)).bundle
    assert bundle.capability_registry.updated_at == "2026-07-24"
    assert isinstance(bundle.capability_registry.updated_at, str)
