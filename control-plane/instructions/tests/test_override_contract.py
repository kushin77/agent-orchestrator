"""Acceptance criterion 2 — contract freeze on customizable fields.

The tenant override contract is closed: an override may customise ONLY the
schema-bounded fields (branding + extra local rules).  Any field outside the
frozen contract — or any attempt to redefine governed behaviour — is REJECTED.
"""

from __future__ import annotations

import copy
import json
import os

import pytest

from aoi.model import load_canonical
from aoi.override import (
    OverrideError,
    apply_local_rules,
    check_override_applies,
    validate_override,
)

_INSTR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_EXAMPLE = os.path.join(_INSTR, "example")
_CANONICAL = os.path.join(_EXAMPLE, "canonical.yaml")
_OVERRIDE = os.path.join(_EXAMPLE, "tenant-override.json")
_SCHEMA_DIR = os.path.join(_INSTR, "schema")


@pytest.fixture(scope="module")
def canonical():
    return load_canonical(_CANONICAL)


@pytest.fixture(scope="module")
def valid_override():
    with open(_OVERRIDE, encoding="utf-8") as handle:
        return json.load(handle)


def _clone(override):
    return copy.deepcopy(override)


# --- acceptance -------------------------------------------------------------

def test_valid_override_is_accepted_and_applies(canonical, valid_override):
    returned = validate_override(valid_override)
    assert returned["schema"] == "ao.instructions.override/v1"
    rules = apply_local_rules(valid_override, canonical)
    assert [rule["id"] for rule in rules] == ["acme-feature-branches", "acme-reviewer-required"]


def test_schema_documents_are_valid_json_schema():
    jsonschema = pytest.importorskip("jsonschema")
    for name in ("canonical.schema.json", "tenant-override.schema.json",
                 "distribution-manifest.schema.json", "consumer-state.schema.json"):
        with open(os.path.join(_SCHEMA_DIR, name), encoding="utf-8") as handle:
            document = json.load(handle)
        # check_schema raises if the document is not a valid JSON Schema.
        jsonschema.Draft202012Validator.check_schema(document)


# --- frozen-contract negatives (schema-bounded rejection) --------------------

@pytest.mark.parametrize("mutator,detail", [
    (lambda o: o.update({"modifyGovernedRules": []}), "unknown top-level field"),
    (lambda o: o["local"].update({"rewritePlatform": True}), "unknown field under local"),
    (lambda o: o["branding"].update({"accentColor": "#0af"}), "unknown field under branding"),
    (lambda o: o["canonical"].update({"owner": "x"}), "unknown field under canonical"),
    (lambda o: o.update({"schema": "ao.instructions.override/v0"}), "unknown schema version"),
    (lambda o: o.update({"tenant": "Acme Corp!"}), "tenant outside the allowed pattern"),
    (lambda o: o["local"]["extraRules"].append({"id": "acme-feature-branches", "text": "duplicate"}), "duplicate extra rule id"),
    (lambda o: o["local"]["extraRules"].append({"id": "x", "text": "short"}), "extra rule text below the minimum"),
    (lambda o: o["local"]["extraRules"].append({"id": "BadRule", "text": "id outside the allowed pattern"}), "extra rule id outside the pattern"),
])
def test_override_outside_frozen_contract_is_rejected(canonical, valid_override, mutator, detail):
    override = _clone(valid_override)
    mutator(override)
    with pytest.raises(OverrideError):
        validate_override(override)
    # And the apply path rejects it too.
    with pytest.raises(OverrideError):
        apply_local_rules(override, canonical)


# --- semantic freeze (governed behaviour cannot be redefined) ----------------

def test_extra_rule_cannot_collide_with_a_governed_rule_id(canonical, valid_override):
    override = _clone(valid_override)
    override["local"]["extraRules"].append({"id": "issue-first", "text": "a tenant attempt to redefine a governed rule"})
    validate_override(override)  # schema-legal: id pattern is fine
    with pytest.raises(OverrideError) as excinfo:
        check_override_applies(override, canonical)
    assert "collides with a governed rule id" in str(excinfo.value)


def test_override_must_target_the_exact_canonical_id(canonical, valid_override):
    override = _clone(valid_override)
    override["canonical"]["id"] = "some-other-repo"
    with pytest.raises(OverrideError) as excinfo:
        check_override_applies(override, canonical)
    assert "canonical id" in str(excinfo.value)


def test_override_must_target_the_exact_canonical_version(canonical, valid_override):
    override = _clone(valid_override)
    override["canonical"]["version"] = "0.9.0"
    with pytest.raises(OverrideError) as excinfo:
        check_override_applies(override, canonical)
    assert "canonical version" in str(excinfo.value)


# --- the pure validator agrees with the JSON Schema where both can judge -----

def test_pure_validator_matches_json_schema_decisions(canonical, valid_override):
    jsonschema = pytest.importorskip("jsonschema")
    with open(os.path.join(_SCHEMA_DIR, "tenant-override.schema.json"), encoding="utf-8") as handle:
        document = json.load(handle)
    validator = jsonschema.Draft202012Validator(document)

    cases = []
    cases.append(("valid", _clone(valid_override), True))
    for label, mutator in [
        ("unknown-top", lambda o: o.update({"modifyGovernedRules": []})),
        ("unknown-local", lambda o: o["local"].update({"rewritePlatform": True})),
        ("unknown-branding", lambda o: o["branding"].update({"color": "#fff"})),
        ("bad-schema", lambda o: o.update({"schema": "ao.instructions.override/v0"})),
        ("bad-tenant", lambda o: o.update({"tenant": "Acme Corp!"})),
        ("bad-repo", lambda o: o["branding"].update({"repository": "no-owner-slash"})),
        ("bad-version", lambda o: o["canonical"].update({"version": "1.0"})),
        ("bad-extra-id", lambda o: o["local"]["extraRules"].append({"id": "BadRule", "text": "a valid-length tenant rule text"})),
        ("short-text", lambda o: o["local"]["extraRules"].append({"id": "acme-x", "text": "short"})),
    ]:
        override = _clone(valid_override)
        mutator(override)
        cases.append((label, override, False))

    for label, override, expected in cases:
        schema_ok = validator.is_valid(override)
        try:
            validate_override(override)
            ours_ok = True
        except OverrideError:
            ours_ok = False
        assert ours_ok == expected, f"pure validator disagrees on case {label}"
        assert schema_ok == expected, f"JSON Schema disagrees on case {label}"
