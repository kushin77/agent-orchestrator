"""Impersonation - explicit grant + audit stamp (issue #35, AC #4).

Harvested from capital-underwriting's support path:

- ``ImpersonationGrant`` (id, jti, accountId/tenant, operatorUserId,
  actingAsUserId, actingAsRole, expiresAt, revokedAt, revokedByUserId,
  reason) - a durable, addressable record of a live impersonation grant;
- the impersonated **session token's jti is the grant's jti**, so revoking
  the grant kills the token via the jti-keyed revocation list (mirrors
  capital's jti-keyed TokenBlacklist revoke);
- **audit stamp**: audit events emitted inside an impersonated session carry
  the real operator (``AuditLog.impersonatedByUserId`` mirror) alongside the
  impersonated subject.

Fail-closed: impersonation requires an explicit, unexpired, unrevoked grant
matching operator/tenant/target; a session is only ever scoped to the grant's
tenant (no cross-tenant fallback).
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Optional

from .errors import (
    GrantExpiredError,
    GrantRevokedError,
    ImpersonationError,
    ImpersonationNotGrantedError,
    UnknownGrantError,
)
from .model import (
    API_SESSION_PURPOSE,
    EVENT_IMP_GRANT,
    EVENT_IMP_LOGIN,
    EVENT_IMP_REVOKE,
    ImpersonationGrant,
    SsoAuditEvent,
    SsoSession,
    utcnow_iso,
)
from .store import InMemoryStore
from .tokens import issue_session_token

DEFAULT_IMP_TTL_SECONDS = 900  # 15-minute enterprise support window


def _grant_active(
    store: InMemoryStore,
    grant_id: str,
    *,
    operator_user_id: str,
    tenant_id: str,
    target_user_id: str,
    now: int,
) -> ImpersonationGrant:
    grant = store.get_grant(grant_id)
    if grant is None:
        raise UnknownGrantError(f"no impersonation grant {grant_id!r}")
    if grant.revoked:
        raise GrantRevokedError(f"impersonation grant {grant_id!r} was revoked")
    if now > grant.expires_at:
        raise GrantExpiredError(f"impersonation grant {grant_id!r} has expired")
    if (
        grant.operator_user_id != operator_user_id
        or grant.tenant_id != tenant_id
        or grant.target_user_id != target_user_id
    ):
        raise ImpersonationNotGrantedError(
            f"grant {grant_id!r} does not cover operator "
            f"{operator_user_id!r} impersonating {target_user_id!r} in "
            f"tenant {tenant_id!r}"
        )
    return grant


def create_impersonation_grant(
    store: InMemoryStore,
    *,
    tenant_id: str,
    operator_user_id: str,
    target_user_id: str,
    target_role: str,
    reason: str,
    now: Optional[int] = None,
    ttl: int = DEFAULT_IMP_TTL_SECONDS,
    grant_id: Optional[str] = None,
) -> ImpersonationGrant:
    """Record an explicit impersonation grant (enterprise support path).

    The grant binds an operator to act as a target user inside one tenant for
    ``ttl`` seconds, for a stated ``reason``. Nothing about the session is
    possible until this explicit grant exists.
    """
    if not tenant_id or not operator_user_id or not target_user_id:
        raise ImpersonationError(
            "tenant, operator and target are all required for impersonation"
        )
    if operator_user_id == target_user_id:
        raise ImpersonationError("operator cannot impersonate itself")
    now_i = int(now if now is not None else time.time())
    grant = ImpersonationGrant(
        grant_id=grant_id or f"imp_{uuid.uuid4().hex}",
        tenant_id=tenant_id,
        operator_user_id=operator_user_id,
        target_user_id=target_user_id,
        target_role=target_role,
        jti=f"jit_{uuid.uuid4().hex}",
        reason=reason,
        expires_at=now_i + int(ttl),
        created_at=now_i,
    )
    store.put_grant(grant)
    store.add_audit(
        SsoAuditEvent(
            event=EVENT_IMP_GRANT,
            tenant_id=tenant_id,
            subject_id=operator_user_id,
            at=utcnow_iso(),
            operator_user_id=operator_user_id,
            grant_id=grant.grant_id,
            detail=f"impersonation grant: {operator_user_id} -> "
            f"{target_user_id} ({reason})",
        )
    )
    return grant


def issue_impersonated_session(
    store: InMemoryStore,
    *,
    hmac_key: bytes,
    grant_id: str,
    operator_user_id: str,
    tenant_id: str,
    target_user_id: str,
    now: Optional[int] = None,
) -> SsoSession:
    """Issue the impersonated session bound to an active explicit grant.

    The session token's ``jti`` equals the grant's ``jti`` and carries the
    operator + grant claims, so (a) revoking the grant revokes the session and
    (b) any downstream audit of the session can stamp the real operator.
    """
    now_i = int(now if now is not None else time.time())
    grant = _grant_active(
        store,
        grant_id,
        operator_user_id=operator_user_id,
        tenant_id=tenant_id,
        target_user_id=target_user_id,
        now=now_i,
    )
    token, claims = issue_session_token(
        hmac_key,
        tenant_id=grant.tenant_id,
        subject_id=grant.target_user_id,
        subject_type="user",
        role=grant.target_role,
        email=grant.target_user_id,
        now=now_i,
        ttl=max(1, grant.expires_at - now_i),
        jti=grant.jti,
        purpose=API_SESSION_PURPOSE,
        operator_user_id=grant.operator_user_id,
        grant_id=grant.grant_id,
    )
    session = SsoSession(
        token=token,
        tenant_id=grant.tenant_id,
        subject_id=grant.target_user_id,
        subject_type="user",
        role=grant.target_role,
        email=grant.target_user_id,
        session_id=str(claims["jti"]),
        purpose=API_SESSION_PURPOSE,
        issued_at=int(claims["iat"]),
        expires_at=int(claims["exp"]),
        operator_user_id=grant.operator_user_id,
        grant_id=grant.grant_id,
    )
    store.add_audit(
        SsoAuditEvent(
            event=EVENT_IMP_LOGIN,
            tenant_id=grant.tenant_id,
            subject_id=grant.target_user_id,
            at=utcnow_iso(),
            session_id=session.session_id,
            operator_user_id=grant.operator_user_id,
            grant_id=grant.grant_id,
            detail="impersonated session issued",
        )
    )
    return session


def revoke_impersonation(
    store: InMemoryStore,
    *,
    grant_id: str,
    revoked_by_user_id: str,
    now: Optional[int] = None,
) -> ImpersonationGrant:
    """Revoke an impersonation grant; its bound session jti is killed."""
    grant = store.get_grant(grant_id)
    if grant is None:
        raise UnknownGrantError(f"no impersonation grant {grant_id!r}")
    now_i = int(now if now is not None else time.time())
    revoked = ImpersonationGrant(
        grant_id=grant.grant_id,
        tenant_id=grant.tenant_id,
        operator_user_id=grant.operator_user_id,
        target_user_id=grant.target_user_id,
        target_role=grant.target_role,
        jti=grant.jti,
        reason=grant.reason,
        expires_at=grant.expires_at,
        created_at=grant.created_at,
        revoked_at=now_i,
        revoked_by_user_id=revoked_by_user_id,
    )
    store.put_grant(revoked)
    store.revoke_jti(revoked.jti, now_i)
    store.add_audit(
        SsoAuditEvent(
            event=EVENT_IMP_REVOKE,
            tenant_id=grant.tenant_id,
            subject_id=grant.operator_user_id,
            at=utcnow_iso(),
            operator_user_id=revoked_by_user_id,
            grant_id=grant.grant_id,
            detail=f"impersonation grant revoked by {revoked_by_user_id}",
        )
    )
    return revoked


def audit_operator(claims: dict[str, Any]) -> Optional[str]:
    """The real operator behind verified session claims (audit stamp)."""
    return claims.get("operatorUserId")


def audit_grant(claims: dict[str, Any]) -> Optional[str]:
    """The impersonation grant behind verified session claims."""
    return claims.get("impersonationGrantId")
