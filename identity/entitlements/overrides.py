"""The override tier: time-boxed, authority-gated departures from the plan.

The catalog answers "what does this *plan* grant"; an override answers "...
and what did we agree with *this tenant*" - a negotiated raise above plan, a
temporary limit bump, a grandfathered capability (saas-rbac ``overrides.ts``
doctrine). It is an exception, and an exception is what governance is for:

- **Authority** - granting or revoking requires the grantor to hold the
  override-authority permission (``entitlement:override``) in the org, as
  evaluated through the RBAC contract (identity/rbac): an out-of-scope
  subject, or one whose roles do not grant it, is refused
  (``OverrideAuthorityError``). Wildcards count, so the built-in preset packs
  confer it on ``owner`` via ``*:*``; a custom pack may grant it to whichever
  role it chooses.
- **Time-boxed** - an override must name a future ``expires_at``; there are no
  permanent overrides. It is only honored while unexpired: an expired override
  no longer grants anything.
- **Audited** - every grant and revoke appends an immutable
  ``EntitlementAuditEvent`` naming who acted, on what, and until when.
- **Whole answer** - an override states the entire answer for its feature
  (``enabled: false`` is a real revocation; it never inherits from the plan),
  so a plan change underneath it cannot silently shift a negotiated agreement
  (downgrade-safe). Only one active override may exist per (org, feature); a
  re-grant supersedes the previous one.
"""

from __future__ import annotations

from rbac.model import ScopeNode, permission_granted
from rbac.resolve import effective_permissions

from entitlements.errors import (
    NoPlanError,
    OverrideAuthorityError,
    OverrideExpiryError,
    UnknownFeatureError,
)
from entitlements.engine import _active_overrides
from entitlements.model import (
    ACTION_OVERRIDE_GRANT,
    ACTION_OVERRIDE_REVOKE,
    FEATURE_KIND_LIMIT,
    OVERRIDE_AUTHORITY_PERMISSION,
    EntitlementAuditEvent,
    Override,
    is_expired,
    to_iso,
    utcnow_iso,
)


def _holds_authority(rbac_store, subject: str, org_id: str) -> bool:
    """Whether ``subject`` holds the override-authority permission in ``org_id``.

    Evaluated through the RBAC contract exactly like any other permission: the
    scope gate first (a subject with no binding in the org holds nothing), then
    the permission match honoring wildcards. An out-of-scope subject is never
    an override authority.
    """
    node = ScopeNode(org_id=org_id)
    granted = effective_permissions(rbac_store, subject, node)
    return any(
        permission_granted(p, OVERRIDE_AUTHORITY_PERMISSION) for p in granted
    )


def grant_override(
    store,
    rbac_store,
    catalog,
    org_id: str,
    feature: str,
    *,
    enabled: bool = True,
    limit: int | None = None,
    expires_at: str,
    granted_by: str,
    note: str = "",
    now: str | None = None,
) -> Override:
    """Grant a time-boxed override of ``feature`` for an org (audited).

    Refusals (all fail-closed, all before any write):

    - no entitlement profile (the org has no plan) -> ``NoPlanError``;
    - unknown feature (not in the catalog)        -> ``UnknownFeatureError``;
    - grantor without the override authority      -> ``OverrideAuthorityError``;
    - ``expires_at`` missing, unparseable or not in the future
                                                   -> ``OverrideExpiryError``
                                                     (no permanent overrides);
    - a numeric limit on a non-limit feature, or a non-positive limit
                                                   -> ``ValueError``.

    Idempotent-by-supersession: if an override already exists for this
    (org, feature), the new grant replaces it and the old one is removed, so
    there is never more than one active answer for a feature.
    """
    if not org_id:
        raise ValueError("org_id must be non-empty")
    profile = store.profile(org_id)
    if profile is None:
        raise NoPlanError(org_id)
    feature_def = catalog.feature(feature)
    if feature_def is None:
        raise UnknownFeatureError(feature)
    if not granted_by:
        raise ValueError("granted_by must be non-empty (audit integrity)")
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be a boolean")
    if limit is not None:
        if feature_def.kind != FEATURE_KIND_LIMIT:
            raise ValueError(
                f"feature {feature!r} is {feature_def.kind!r}-kind and cannot carry "
                "a numeric override limit"
            )
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError(f"override limit must be a positive integer, got {limit!r}")
    ts = now or utcnow_iso()
    expiry = to_iso(expires_at)  # raises ValueError on garbage
    if is_expired(expiry, ts):
        raise OverrideExpiryError(org_id, feature, expiry)
    if not _holds_authority(rbac_store, granted_by, org_id):
        raise OverrideAuthorityError(granted_by, org_id, OVERRIDE_AUTHORITY_PERMISSION)

    # Supersede any existing override for this (org, feature).
    superseded: list[str] = []
    for existing in _active_overrides(store, org_id, ts):
        if existing.feature == feature:
            superseded.append(existing.id)
            store.delete_override(existing.id)

    override = Override(
        id=store._new_id("ovr"),
        org_id=org_id,
        feature=feature,
        enabled=enabled,
        limit=limit,
        expires_at=expiry,
        note=note,
        granted_by=granted_by,
        granted_at=ts,
    )
    store.add_override(override)

    detail = (
        f"feature={feature} enabled={enabled}"
        f"{' limit=' + str(limit) if limit is not None else ''}"
        f" expires_at={expiry}"
        f"{' note=' + note if note else ''}"
    )
    if superseded:
        detail = f"{detail} supersedes={','.join(superseded)}"
    store.record_event(
        EntitlementAuditEvent(
            id=store._new_id("evt"),
            org_id=org_id,
            at=ts,
            actor=granted_by,
            action=ACTION_OVERRIDE_GRANT,
            detail=detail,
        )
    )
    return override


def revoke_override(
    store,
    rbac_store,
    org_id: str,
    override_id: str,
    *,
    revoked_by: str,
    now: str | None = None,
) -> bool:
    """Revoke an override before it expires (audited).

    Returns False when no such override exists (idempotent no-op). Refuses with
    ``OverrideAuthorityError`` when ``revoked_by`` lacks the override-authority
    permission - revoking is as sensitive as granting. Revoking an already
    expired override is allowed (cleanup of an inert row) but still requires
    authority, because it mutates state.
    """
    override = store.override(override_id)
    if override is None:
        return False
    if not revoked_by:
        raise ValueError("revoked_by must be non-empty (audit integrity)")
    if not _holds_authority(rbac_store, revoked_by, org_id):
        raise OverrideAuthorityError(revoked_by, org_id, OVERRIDE_AUTHORITY_PERMISSION)

    ts = now or utcnow_iso()
    store.delete_override(override_id)
    store.record_event(
        EntitlementAuditEvent(
            id=store._new_id("evt"),
            org_id=org_id,
            at=ts,
            actor=revoked_by,
            action=ACTION_OVERRIDE_REVOKE,
            detail=(
                f"feature={override.feature} expires_at={override.expires_at}"
                f" granted_at={override.granted_at}"
            ),
        )
    )
    return True
