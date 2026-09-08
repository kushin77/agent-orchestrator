"""The override tier: authority-gated, time-boxed, audited departures.

Negative invariants under test:

- an unexpired override attempted without the override-authority permission is
  denied (``OverrideAuthorityError``), no matter what other power the caller
  holds;
- an override must expire in the future (no permanent overrides);
- an expired override no longer grants anything;
- an override states the whole answer for its feature (can revoke what the
  plan grants); a plan downgrade cannot cancel a granted override
  (downgrade-safe); an override may only name a catalog feature.
"""

import pytest

import entitlements as E
from rbac import ScopeNode
from rbac.bindings import grant_role
from rbac.presets import seed_org

from helpers import iso, provision_org

ORG = "acme"
OWNER = "owner@acme.test"
MEMBER = "member@acme.test"


def _org_node() -> ScopeNode:
    return ScopeNode(org_id=ORG)


def _seed_org_only(rbac_store, org_id: str = ORG):
    """Create an RBAC org with preset roles but no owner binding."""
    org = rbac_store.add_org(org_id, org_id.title(), tenant_type="enterprise")
    seed_org(rbac_store, org)
    return org


def test_grant_requires_override_authority(rbac_store, estore, catalog):
    provision_org(rbac_store, estore, catalog, plan="free")
    grant_role(rbac_store, ORG, MEMBER, "member")
    # A member - who holds ordinary powers but not entitlement:override - is denied.
    with pytest.raises(E.OverrideAuthorityError):
        E.grant_override(
            estore, rbac_store, catalog, ORG, "audit_log",
            enabled=True, expires_at=iso(3600), granted_by=MEMBER, now=iso(),
        )
    # A subject with no binding in the org at all is out of scope -> no authority.
    with pytest.raises(E.OverrideAuthorityError):
        E.grant_override(
            estore, rbac_store, catalog, ORG, "audit_log",
            enabled=True, expires_at=iso(3600), granted_by="outsider@x.test", now=iso(),
        )
    assert estore.overrides_for_org(ORG) == []


def test_authority_holder_can_grant_timeboxed_override(rbac_store, estore, catalog):
    provision_org(rbac_store, estore, catalog, plan="free")
    override = E.grant_override(
        estore, rbac_store, catalog, ORG, "audit_log",
        enabled=True, expires_at=iso(3600), granted_by=OWNER,
        note="enterprise pilot", now=iso(),
    )
    assert override.feature == "audit_log"
    assert override.expires_at == iso(3600)
    # Free + override -> audit_log is entitled.
    assert E.feature_enabled(estore, catalog, ORG, "audit_log", now=iso())


def test_override_must_be_timeboxed_to_the_future(rbac_store, estore, catalog):
    provision_org(rbac_store, estore, catalog, plan="free")
    # An expiry in the past is refused.
    with pytest.raises(E.OverrideExpiryError):
        E.grant_override(
            estore, rbac_store, catalog, ORG, "audit_log",
            enabled=True, expires_at=iso(-10), granted_by=OWNER, now=iso(),
        )
    # A missing expiry is refused (no permanent overrides).
    with pytest.raises(ValueError):
        E.grant_override(
            estore, rbac_store, catalog, ORG, "audit_log",
            enabled=True, expires_at=None, granted_by=OWNER, now=iso(),
        )
    assert estore.overrides_for_org(ORG) == []


def test_expired_override_no_longer_grants(rbac_store, estore, catalog):
    provision_org(rbac_store, estore, catalog, plan="free")
    node = _org_node()
    E.grant_override(
        estore, rbac_store, catalog, ORG, "audit_log",
        enabled=True, expires_at=iso(10), granted_by=OWNER, now=iso(),
    )
    # While unexpired the override grants the out-of-plan feature.
    assert E.feature_enabled(estore, catalog, ORG, "audit_log", now=iso(5))
    assert E.is_allowed(estore, rbac_store, catalog, ORG, OWNER, node, "audit:read", now=iso(5))
    # Once expired it grants nothing (fail closed; the plan still denies).
    assert not E.feature_enabled(estore, catalog, ORG, "audit_log", now=iso(11))
    decision = E.evaluate_permission(
        estore, rbac_store, catalog, ORG, OWNER, node, "audit:read", now=iso(11)
    )
    assert decision.denied and decision.reason == "entitlement"


def test_override_whole_answer_can_revoke_plan_feature(rbac_store, estore, catalog):
    provision_org(rbac_store, estore, catalog, plan="enterprise")
    node = _org_node()
    # Enterprise grants audit_log.
    assert E.is_allowed(estore, rbac_store, catalog, ORG, OWNER, node, "audit:read", now=iso())
    # An override is the whole answer: enabled=false is a real revocation.
    override = E.grant_override(
        estore, rbac_store, catalog, ORG, "audit_log",
        enabled=False, expires_at=iso(3600), granted_by=OWNER,
        note="regulatory pause", now=iso(),
    )
    assert not E.feature_enabled(estore, catalog, ORG, "audit_log", now=iso())
    denied = E.evaluate_permission(
        estore, rbac_store, catalog, ORG, OWNER, node, "audit:read", now=iso()
    )
    assert denied.denied and denied.reason == "entitlement"
    # Revoking the override restores the plan's grant.
    assert E.revoke_override(estore, rbac_store, ORG, override.id, revoked_by=OWNER, now=iso()) is True
    assert E.is_allowed(estore, rbac_store, catalog, ORG, OWNER, node, "audit:read", now=iso())


def test_downgrade_safe_override_survives_plan_change(rbac_store, estore, catalog):
    provision_org(rbac_store, estore, catalog, plan="enterprise")
    node = _org_node()
    assert E.feature_enabled(estore, catalog, ORG, "sso", now=iso())
    # Grant a negotiated sso override, then downgrade to free.
    override = E.grant_override(
        estore, rbac_store, catalog, ORG, "sso",
        enabled=True, expires_at=iso(7200), granted_by=OWNER,
        note="grandfathered", now=iso(),
    )
    E.assign_plan(estore, catalog, ORG, "free", actor=OWNER, now=iso())
    # Free no longer grants sso - but the override keeps it until it expires.
    assert E.feature_enabled(estore, catalog, ORG, "sso", now=iso(10))
    # After expiry, the downgrade takes effect (sso is off on free).
    assert not E.feature_enabled(estore, catalog, ORG, "sso", now=iso(7201))
    # The override row still exists (cleanup is an explicit revoke).
    assert estore.override(override.id) is not None


def test_override_unknown_feature_rejected(rbac_store, estore, catalog):
    provision_org(rbac_store, estore, catalog, plan="pro")
    with pytest.raises(E.UnknownFeatureError):
        E.grant_override(
            estore, rbac_store, catalog, ORG, "no-such-feature",
            enabled=True, expires_at=iso(3600), granted_by=OWNER, now=iso(),
        )


def test_override_limit_respects_feature_kind(rbac_store, estore, catalog):
    provision_org(rbac_store, estore, catalog, plan="free")
    # A numeric limit is only legal on a limit-kind feature.
    with pytest.raises(ValueError):
        E.grant_override(
            estore, rbac_store, catalog, ORG, "sso",
            enabled=True, limit=5, expires_at=iso(3600), granted_by=OWNER, now=iso(),
        )
    # Raising managed_agents above the free cap is the canonical limit override.
    override = E.grant_override(
        estore, rbac_store, catalog, ORG, "managed_agents",
        enabled=True, limit=50, expires_at=iso(3600), granted_by=OWNER, now=iso(),
    )
    assert override.limit == 50
    assert E.limit_for(estore, catalog, ORG, "managed_agents", now=iso()) == 50


def test_regrant_supersedes_previous_override(rbac_store, estore, catalog):
    provision_org(rbac_store, estore, catalog, plan="free")
    first = E.grant_override(
        estore, rbac_store, catalog, ORG, "managed_agents",
        enabled=True, limit=10, expires_at=iso(3600), granted_by=OWNER, now=iso(),
    )
    second = E.grant_override(
        estore, rbac_store, catalog, ORG, "managed_agents",
        enabled=True, limit=50, expires_at=iso(7200), granted_by=OWNER, now=iso(1),
    )
    # Exactly one active override remains, the newest.
    active = estore.overrides_for_org(ORG)
    assert [o.id for o in active] == [second.id]
    assert estore.override(first.id) is None
    assert E.limit_for(estore, catalog, ORG, "managed_agents", now=iso()) == 50
    # Two grant events recorded; the later one names the superseded id.
    events = estore.events_for_org(ORG)
    grants = [e for e in events if e.action == E.ACTION_OVERRIDE_GRANT]
    assert len(grants) == 2
    assert first.id in grants[1].detail


def test_revoke_requires_authority_and_is_idempotent(rbac_store, estore, catalog):
    provision_org(rbac_store, estore, catalog, plan="free")
    grant_role(rbac_store, ORG, MEMBER, "member")
    override = E.grant_override(
        estore, rbac_store, catalog, ORG, "audit_log",
        enabled=True, expires_at=iso(3600), granted_by=OWNER, now=iso(),
    )
    # A member cannot revoke either.
    with pytest.raises(E.OverrideAuthorityError):
        E.revoke_override(estore, rbac_store, ORG, override.id, revoked_by=MEMBER, now=iso())
    # The authority holder can; revoking an absent override is a no-op (False).
    assert E.revoke_override(estore, rbac_store, ORG, override.id, revoked_by=OWNER, now=iso()) is True
    assert E.revoke_override(estore, rbac_store, ORG, override.id, revoked_by=OWNER, now=iso()) is False
    assert estore.overrides_for_org(ORG) == []


def test_override_requires_an_existing_plan_profile(rbac_store, estore, catalog):
    # An RBAC org with no plan assignment cannot take an override (fail closed).
    _seed_org_only(rbac_store)
    with pytest.raises(E.NoPlanError):
        E.grant_override(
            estore, rbac_store, catalog, ORG, "audit_log",
            enabled=True, expires_at=iso(3600), granted_by=OWNER, now=iso(),
        )
