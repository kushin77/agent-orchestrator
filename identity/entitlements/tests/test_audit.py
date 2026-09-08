"""Audit of plan/override changes (issue #36, deliverable 5).

Every plan assignment and every override grant/revoke appends an immutable,
per-org audit event naming who acted, on what, when, and (for overrides) until
when. The audit log is append-order (the record of record).
"""

import entitlements as E

from helpers import iso, provision_org

ORG = "acme"
OWNER = "owner@acme.test"


def _provision(rbac_store, estore, catalog):
    provision_org(rbac_store, estore, catalog, plan="free", owner=OWNER)
    return estore.events_for_org(ORG)


def test_plan_assignment_is_audited(rbac_store, estore, catalog):
    events = _provision(rbac_store, estore, catalog)
    assert len(events) == 1
    event = events[0]
    assert event.org_id == ORG
    assert event.actor == OWNER
    assert event.action == E.ACTION_PLAN_ASSIGN
    assert event.at == iso()
    assert "plan=free" in event.detail


def test_plan_change_is_audited(rbac_store, estore, catalog):
    _provision(rbac_store, estore, catalog)
    E.assign_plan(estore, catalog, ORG, "pro", actor=OWNER, note="growth", now=iso(10))
    events = estore.events_for_org(ORG)
    assert [e.action for e in events] == [E.ACTION_PLAN_ASSIGN, E.ACTION_PLAN_ASSIGN]
    assert "plan=pro" in events[1].detail
    assert "note=growth" in events[1].detail
    assert events[1].at == iso(10)
    # The profile reflects the change; the audit trail keeps both records.
    assert estore.profile(ORG).plan_key == "pro"


def test_override_grant_and_revoke_are_audited(rbac_store, estore, catalog):
    _provision(rbac_store, estore, catalog)
    override = E.grant_override(
        estore, rbac_store, catalog, ORG, "audit_log",
        enabled=True, expires_at=iso(3600), granted_by=OWNER,
        note="pilot", now=iso(5),
    )
    assert E.revoke_override(estore, rbac_store, ORG, override.id, revoked_by=OWNER, now=iso(20)) is True

    events = estore.events_for_org(ORG)
    actions = [e.action for e in events]
    assert actions == [
        E.ACTION_PLAN_ASSIGN,
        E.ACTION_OVERRIDE_GRANT,
        E.ACTION_OVERRIDE_REVOKE,
    ]
    grant, revoke = events[1], events[2]
    assert grant.actor == OWNER and grant.action == E.ACTION_OVERRIDE_GRANT
    assert "feature=audit_log" in grant.detail
    assert "enabled=True" in grant.detail
    assert "expires_at=" in grant.detail
    assert "note=pilot" in grant.detail
    assert grant.at == iso(5)
    assert revoke.actor == OWNER and revoke.action == E.ACTION_OVERRIDE_REVOKE
    assert "feature=audit_log" in revoke.detail
    assert revoke.at == iso(20)


def test_audit_is_scoped_per_org(rbac_store, estore, catalog):
    provision_org(rbac_store, estore, catalog, plan="free", owner=OWNER)
    provision_org(rbac_store, estore, catalog, org_id="other", plan="free", owner="owner@other.test")
    assert len(estore.events_for_org(ORG)) == 1
    assert len(estore.events_for_org("other")) == 1
    assert all(e.org_id == "other" for e in estore.events_for_org("other"))
