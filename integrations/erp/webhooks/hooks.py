"""Disposition of every CRM hook the PF-2 inventory names (issue #671 acceptance).

Issue #671's acceptance criterion reads: "Every CRM hook PF-2 inventoried has a
bridge (or an explicit disposition)." PF-2 (#667,
``docs/erp-finops/token-baseline.md``, section "CRM conversion-hook
inventory") measured this checkout and recorded two things:

1. five hooks that **exist in-repo** — none of them a CRM conversion event.
   They are the control plane's own signup/entitlement path
   (``identity/onboarding``, ``identity/entitlements``,
   ``portal/server/state.py`` subscription status, the trial-pause approval
   gate, and provisioning/activation audit) and none of them is owned by this
   lane's file allowlist (``integrations/erp/webhooks/**``) or represents a
   sale converting to a ledger entry.
2. that **no external CRM webhook exists in-tree** — PF-2 marked that
   CANNOT-ASSESS and named this lane (#671) as where it would land.

So the disposition this module ships is: the five in-repo hooks are
**out-of-scope, by disposition** (recorded below, each with why); and the
*bridgeable* surface this lane actually builds is the two conversion events the
ERP module's own CRM lane already emits (``integrations/erp/crm/flows.py``:
``convert_lead`` and ``win_opportunity``), which are the only in-tree events
that represent a CRM conversion. Both are declared in
:mod:`.model` (``HOOK_LEAD_CONVERSION``, ``HOOK_OPPORTUNITY_WIN``) and both
have a bridge in :mod:`.bridge`.

``tests/test_hooks_inventory.py`` parses the PF-2 table out of
``docs/erp-finops/token-baseline.md`` and asserts every hook name it lists
appears in :data:`PF2_DISPOSITIONS` below — so a PF-2 edit that adds a hook
without a matching disposition here fails the suite instead of silently
under-covering the acceptance criterion.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

#: Disposition codes. A hook is either bridged here, or explicitly
#: out-of-scope with a reason — there is no third, undocumented state.
DISPOSITION_BRIDGED = "bridged"
DISPOSITION_OUT_OF_SCOPE = "out-of-scope"

DISPOSITIONS: Tuple[str, ...] = (DISPOSITION_BRIDGED, DISPOSITION_OUT_OF_SCOPE)


@dataclass(frozen=True)
class HookDisposition:
    """One PF-2 hook, and this lane's explicit disposition for it."""

    hook: str
    disposition: str
    reason: str

    def __post_init__(self) -> None:
        if self.disposition not in DISPOSITIONS:
            raise ValueError(f"{self.disposition!r} is not a declared disposition")


#: Every hook PF-2's "CRM conversion-hook inventory" table names, mapped to its
#: disposition. Keyed by the exact "Hook" column text from
#: ``docs/erp-finops/token-baseline.md`` so the cross-check in
#: ``tests/test_hooks_inventory.py`` can match by substring without guessing.
PF2_DISPOSITIONS: Dict[str, HookDisposition] = {
    "Tenant lifecycle": HookDisposition(
        hook="Tenant lifecycle",
        disposition=DISPOSITION_OUT_OF_SCOPE,
        reason=(
            "identity/onboarding provisioning state, not a CRM conversion event; "
            "no sale occurs at tenant activation, so no ledger entry is owed."
        ),
    ),
    "Plan → entitlement → RBAC": HookDisposition(
        hook="Plan → entitlement → RBAC",
        disposition=DISPOSITION_OUT_OF_SCOPE,
        reason=(
            "identity/entitlements RBAC grant, not a CRM conversion; entitlement "
            "changes are not priced events in this checkout."
        ),
    ),
    "Subscription status": HookDisposition(
        hook="Subscription status",
        disposition=DISPOSITION_OUT_OF_SCOPE,
        reason=(
            "portal/server/state.py Tenant.subscription_status flips trial/active; "
            "billing for a subscription is a separate, unbuilt lane, not this "
            "conversion-event bridge."
        ),
    ),
    "Trial pause (approval-gated)": HookDisposition(
        hook="Trial pause (approval-gated)",
        disposition=DISPOSITION_OUT_OF_SCOPE,
        reason=(
            "portal/server/app.py trial-hold is an approval workflow trigger, not "
            "a completed conversion; nothing has sold yet when it fires."
        ),
    ),
    "Provisioning / activation audit": HookDisposition(
        hook="Provisioning / activation audit",
        disposition=DISPOSITION_OUT_OF_SCOPE,
        reason=(
            "identity/cpapi provisioning/activation audit events record platform "
            "state changes, not accounting-relevant conversions."
        ),
    ),
    "External CRM conversion hooks": HookDisposition(
        hook="External CRM conversion hooks (Salesforce/HubSpot/etc.)",
        disposition=DISPOSITION_OUT_OF_SCOPE,
        reason=(
            "PF-2 recorded CANNOT-ASSESS: no external CRM integration exists "
            "in-tree to hold a webhook contract for. This lane ships the bridge "
            "surface (schema/auth/idempotency/retry) an external sender would "
            "target, and bridges the two in-tree CRM-family conversion events "
            "instead: integrations/erp/crm/flows.py convert_lead and "
            "win_opportunity, declared as HOOK_LEAD_CONVERSION and "
            "HOOK_OPPORTUNITY_WIN in .model."
        ),
    ),
}
