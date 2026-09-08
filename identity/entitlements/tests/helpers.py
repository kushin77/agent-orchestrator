"""Shared test helpers for the entitlements contract (issue #36)."""

from datetime import datetime, timezone, timedelta

import entitlements as E
from entitlements.model import to_iso


T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def iso(offset_seconds: int = 0) -> str:
    """ISO timestamp at a fixed base plus ``offset_seconds`` (deterministic)."""
    return to_iso(T0 + timedelta(seconds=offset_seconds))


def provision_org(
    rbac_store,
    estore,
    catalog,
    org_id: str = "acme",
    tenant_type: str = "enterprise",
    plan: str = "free",
    owner: str = "owner@acme.test",
    *,
    actor: str | None = None,
):
    """Create an RBAC org (seeded preset roles + owner) and assign a plan.

    tenant_type selects the RBAC preset pack (issue #12) and is deliberately
    orthogonal to the subscription plan: an enterprise-shaped org may sit on a
    free plan, which is exactly the capability-tiering case under test.
    """
    org = rbac_store.add_org(org_id, org_id.title(), tenant_type=tenant_type)
    from rbac.presets import seed_org

    seed_org(rbac_store, org)
    from rbac.bindings import grant_role

    grant_role(rbac_store, org_id, owner, "owner")
    E.assign_plan(
        estore,
        catalog,
        org_id,
        plan,
        actor=actor or owner,
        now=iso(),
    )
    return org
