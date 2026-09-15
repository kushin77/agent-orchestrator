"""The model's closed vocabularies, its refusal type, and the GR-6 property."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from integrations.erp.auth import contract
from integrations.erp.auth.model import (
    ACTIONS,
    REFUSALS,
    SCHEMA_VERSION,
    TENANT_LOCAL_ACTIONS,
    Decision,
    Principal,
    Refused,
    Request,
)


def test_schema_version_is_declared():
    assert SCHEMA_VERSION == 1


def test_the_action_vocabulary_is_closed_and_all_tenant_local():
    assert ACTIONS == tuple(sorted(ACTIONS))
    # Every action is tenant-local: an action that moved data *between* tenants
    # could not be granted without contradicting the tenant gate, so there is no
    # such action to grant.
    assert TENANT_LOCAL_ACTIONS == ACTIONS
    assert "share" not in ACTIONS and "export" not in ACTIONS


def test_a_refusal_outside_the_vocabulary_fails_at_the_raise():
    # The vocabulary is closed in practice, not in prose: a refusal that invents
    # a code is a bug in the refuser, and it fails here rather than reaching a
    # caller that cannot branch on it.
    with pytest.raises(ValueError, match="not in the closed refusal vocabulary"):
        Refused("invented-code", "should not be constructible")


def test_a_refusal_inside_the_vocabulary_carries_its_code():
    refusal = Refused("cross-tenant", "somewhere else")
    assert refusal.code == "cross-tenant"
    assert "somewhere else" in str(refusal)


def test_a_denied_decision_must_name_a_declared_refusal():
    with pytest.raises(ValueError, match="closed refusal vocabulary"):
        Decision(allowed=False, reason="not-a-code")
    # ...and an allowed decision need not.
    assert Decision(allowed=True).reason == ""


@pytest.mark.parametrize("tenant,subject", [("", "s"), ("t", "")])
def test_a_malformed_principal_is_refused(tenant, subject):
    with pytest.raises(Refused) as caught:
        Principal(tenant=tenant, subject=subject)
    assert caught.value.code == "malformed-principal"


def test_a_principal_carries_no_credential_field_gr6():
    """The property is a shape, not a promise: there is no field to put one in."""
    names = {f.name for f in dataclasses.fields(Principal)}
    assert names == {"tenant", "subject", "roles"}
    for banned in ("token", "secret", "password", "credential", "api_key", "apikey", "bearer"):
        assert not any(banned in name.lower() for name in names), (
            f"Principal grew a {banned!r}-shaped field; GR-6 keeps identity reference-based"
        )


def test_a_principal_with_no_roles_is_legal():
    # It can do nothing, but it is not malformed: the refusal that follows is the
    # action being denied, not the principal being invalid.
    assert Principal(tenant="t", subject="s").roles == ()


def test_a_request_defaults_to_no_fields_and_no_team():
    request = Request(tenant="t", kind="k", action="read")
    assert dict(request.fields) == {}
    assert request.team is None


def test_the_effect_vocabulary_is_read_from_the_guardrails_contract():
    # Consumed, never re-typed: if guardrails renames a level, this changes.
    assert set(contract.effect_vocabulary()) == {"block", "warn", "log"}
    assert contract.is_effect("block")
    assert not contract.is_effect("deny")


def test_an_unreadable_contract_is_cannot_assess_not_a_denial():
    """Order-independent: the vocabulary is read *first*, so a warm cache cannot mask this."""
    contract.effect_vocabulary()  # warm the cache deliberately
    original = contract.DECISION_MODULE_PATH
    contract.DECISION_MODULE_PATH = Path("/nonexistent/decision.py")
    try:
        with pytest.raises(Refused) as caught:
            contract.effect_vocabulary()
        assert caught.value.code == "contract-unavailable"
    finally:
        contract.DECISION_MODULE_PATH = original
    # ...and the vocabulary is readable again once the path is restored.
    assert contract.effect_vocabulary() == ("block", "log", "warn")
