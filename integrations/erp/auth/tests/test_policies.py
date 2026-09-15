"""Field-level policy: the two contradictions, the four shapes, and the record."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from integrations.erp.auth import policies, provenance, schemas
from integrations.erp.auth.model import Refused

ROOT = Path(__file__).resolve().parent.parent


def _rule(**overrides):
    base = {
        "id": "r",
        "kind": "sales-invoice",
        "field": "gross-margin",
        "effect": "block",
        "read": False,
        "write": False,
        "roles": ["Sales User"],
        "reason": "a finance figure",
    }
    base.update(overrides)
    return base


@pytest.fixture(scope="module")
def policy_set():
    return policies.load_default(kinds=("sales-invoice", "item-master", "purchase-order", "sales-order"))


def test_the_shipped_set_loads(policy_set):
    assert len(policy_set.rules) == 5


def test_enforcement_must_declare_block():
    # A denial declared log would tell guardrails to allow-and-observe an action
    # this rule is about to refuse: the two layers would disagree.
    with pytest.raises(Refused) as caught:
        policies.load({"version": 1, "rules": [_rule(effect="log")]})
    assert caught.value.code == "declaration-invalid"
    assert "disagree" in caught.value.detail


def test_advice_must_not_declare_block():
    # A rule that withholds nothing and declares block blocks nothing: a rule
    # that cannot fail (GR-12).
    with pytest.raises(Refused) as caught:
        policies.load({"version": 1, "rules": [_rule(effect="block", read=True, write=True)]})
    assert caught.value.code == "declaration-invalid"
    assert "formality" in caught.value.detail


def test_an_invented_effect_is_refused_against_the_contract_vocabulary():
    with pytest.raises(Refused) as caught:
        policies.load({"version": 1, "rules": [_rule(effect="deny")]})
    assert caught.value.code == "unknown-effect"
    assert "block" in caught.value.detail and "warn" in caught.value.detail


def test_a_duplicate_rule_id_is_refused():
    with pytest.raises(Refused) as caught:
        policies.load({"version": 1, "rules": [_rule(), _rule()]})
    assert caught.value.code == "unknown-field-policy"


def test_a_field_wildcard_is_refused():
    with pytest.raises(Refused) as caught:
        policies.load({"version": 1, "rules": [_rule(field="gross*")]})
    assert caught.value.code == "unknown-field"


def test_a_rule_without_a_reason_is_refused():
    with pytest.raises(Refused) as caught:
        policies.load({"version": 1, "rules": [_rule(reason="  ")]})
    assert caught.value.code == "declaration-invalid"


def test_a_rule_governing_an_undeclared_kind_is_refused_when_kinds_are_known():
    with pytest.raises(Refused) as caught:
        policies.load({"version": 1, "rules": [_rule()]}, kinds=("sales-order",))
    assert caught.value.code == "unknown-kind"


def test_the_four_shapes_are_expressed(policy_set):
    # hidden: read and write withheld
    assert policy_set.read_denied("sales-invoice", "gross-margin", ["Sales User"]) is not None
    assert policy_set.write_denied("sales-invoice", "gross-margin", ["Sales User"]) is not None
    # write-only: readable no, writable yes
    assert policy_set.read_denied("purchase-order", "supplier-bank-reference", ["Purchase User"]) is not None
    assert policy_set.write_denied("purchase-order", "supplier-bank-reference", ["Purchase User"]) is None
    # read-only: readable yes, writable no
    assert policy_set.read_denied("sales-order", "credit-limit", ["Sales User"]) is None
    assert policy_set.write_denied("sales-order", "credit-limit", ["Sales User"]) is not None
    # advisory: neither withheld
    assert policy_set.read_denied("sales-invoice", "discount-percent", ["Sales User"]) is None
    assert policy_set.advisories("sales-invoice", "discount-percent", ["Sales User"]) == (
        "discount-needs-finance-review",
    )


def test_a_role_scoped_rule_does_not_govern_other_roles(policy_set):
    assert policy_set.read_denied("sales-invoice", "gross-margin", ["Accounts Manager"]) is None
    assert policy_set.read_denied("sales-invoice", "gross-margin", ["Sales User"]) is not None


def test_an_unruled_field_is_governed_by_nothing(policy_set):
    assert policy_set.covering("sales-invoice", "total", ["Sales User"]) == ()


# --- the frozen schemas are used, not decorative ----------------------------


def test_every_shipped_declaration_matches_its_frozen_schema():
    pairs = (
        ("role-map.schema.json", "roles.json"),
        ("field-policy.schema.json", "field-policies.json"),
        ("provenance.schema.json", "provenance.json"),
    )
    for schema_name, decl in pairs:
        instance = json.loads((ROOT / "catalog" / decl).read_text(encoding="utf-8"))
        assert schemas.violations(instance, schema_name) == [], f"{decl} violates {schema_name}"


def test_a_declaration_that_breaks_its_schema_is_refused_by_name():
    with pytest.raises(Refused) as caught:
        schemas.enforce({"version": "one"}, "role-map.schema.json", "a role map")
    assert caught.value.code == "schema-violation"
    assert "role-map.schema.json" in caught.value.detail


def test_a_missing_frozen_schema_is_cannot_assess():
    with pytest.raises(Refused) as caught:
        schemas.load_schema("no-such.schema.json")
    assert caught.value.code == "declaration-invalid"


# --- GR-10 provenance -------------------------------------------------------


def test_the_shipped_record_declares_the_gpl_upstream_as_a_pattern_source():
    record = provenance.load_default()
    assert record.upstream == "frappe/erpnext"
    assert record.license == "GPL-3.0"
    assert record.mode == "pattern-only"
    assert record.harvests


def test_a_harvest_claiming_copied_code_is_refused():
    bad = {
        "schema": "erp.auth.provenance/v1",
        "upstream": "u",
        "license": "GPL-3.0",
        "mode": "code-copied",
        "harvests": [
            {"shape": "s", "upstreamPath": "p", "mode": "pattern-only", "builtInstead": "b"}
        ],
    }
    with pytest.raises(Refused) as caught:
        provenance.load(bad)
    assert caught.value.code == "harvest-code-copied"


def test_a_harvest_mode_claiming_copied_code_is_refused_too():
    bad = {
        "schema": "erp.auth.provenance/v1",
        "upstream": "u",
        "license": "GPL-3.0",
        "mode": "pattern-only",
        "harvests": [{"shape": "s", "upstreamPath": "p", "mode": "code-copied", "builtInstead": "b"}],
    }
    with pytest.raises(Refused) as caught:
        provenance.load(bad)
    assert caught.value.code == "harvest-code-copied"


def test_a_harvest_that_says_what_it_lost_but_not_what_it_built_is_refused():
    bad = {
        "schema": "erp.auth.provenance/v1",
        "upstream": "u",
        "license": "GPL-3.0",
        "mode": "pattern-only",
        "harvests": [{"shape": "s", "upstreamPath": "p", "mode": "pattern-only", "builtInstead": ""}],
    }
    with pytest.raises(Refused) as caught:
        provenance.load(bad)
    assert caught.value.code == "harvest-incomplete"


def test_an_empty_record_is_refused():
    with pytest.raises(Refused) as caught:
        provenance.load(
            {
                "schema": "erp.auth.provenance/v1",
                "upstream": "u",
                "license": "GPL-3.0",
                "mode": "pattern-only",
                "harvests": [],
            }
        )
    assert caught.value.code == "harvest-incomplete"
