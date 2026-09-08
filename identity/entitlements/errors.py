"""Exceptions for the plan -> entitlement -> override contract.

Mutation operations (``assign_plan``, ``grant_override``, ``revoke_override``)
raise these on a refusal so a caller surfaces the cause. Pure evaluation
(``feature_state``, ``evaluate_permission``) never raises for a deny: it
returns a ``FeatureState`` / ``AccessDecision`` carrying a fail-closed code.
"""


class EntitlementError(RuntimeError):
    """Base class for entitlement-contract refusals."""


class UnknownOrgError(EntitlementError):
    """No entitlement profile exists for the org (no plan assignment)."""

    def __init__(self, org_id: str) -> None:
        super().__init__(f"no entitlement profile for org {org_id!r} (no plan assigned)")
        self.org_id = org_id


class NoPlanError(EntitlementError):
    """The org has no plan assignment (fail-closed: a plan is never implied)."""

    def __init__(self, org_id: str) -> None:
        super().__init__(f"org {org_id!r} has no plan assigned")
        self.org_id = org_id


class UnknownPlanError(EntitlementError):
    """The plan key is not in the catalog (fail-closed on unknown plans)."""

    def __init__(self, plan_key: str) -> None:
        super().__init__(f"unknown plan {plan_key!r} (not in the catalog)")
        self.plan_key = plan_key


class UnknownFeatureError(EntitlementError):
    """The feature key is not in the catalog (fail-closed on unknown features)."""

    def __init__(self, feature: str) -> None:
        super().__init__(f"unknown feature {feature!r} (not in the catalog)")
        self.feature = feature


class OverrideAuthorityError(EntitlementError):
    """An override mutation was attempted without the override-authority."""

    def __init__(self, subject: str, org_id: str, permission: str) -> None:
        super().__init__(
            f"subject {subject!r} lacks {permission!r} in org {org_id!r}; "
            "override changes require the override-authority permission"
        )
        self.subject = subject
        self.org_id = org_id
        self.permission = permission


class OverrideExpiryError(EntitlementError):
    """An override must be time-boxed to the future (no permanent overrides)."""

    def __init__(self, org_id: str, feature: str, expires_at: str) -> None:
        super().__init__(
            f"override for org {org_id!r} feature {feature!r} must expire in the "
            f"future, got expires_at {expires_at!r}"
        )
        self.org_id = org_id
        self.feature = feature
        self.expires_at = expires_at
