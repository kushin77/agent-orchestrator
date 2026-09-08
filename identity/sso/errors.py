"""SSO error taxonomy - tenant identity + SSO (issue #35).

Every failure mode in this lane is a typed, fail-closed error. The names
deliberately mirror the contracts this lane consumes so an HTTP/control-plane
layer (a later identity phase) can map them to responses without re-reading
this subtree:

- saas-rbac ``authenticate.ts`` ``AUTH_ERRORS``: ``unknown_tenant``,
  ``missing_bearer_token``, ``invalid_token``, ``tenant_mismatch``,
  ``profile_unavailable`` (host->tenant resolution + token tenant cross-check).
- registry/service (issue #10): ``CrossTenantDenied``, ``SessionExpiredError``,
  ``InvalidCredentialError`` (scoped-claims session tokens).
- capital-underwriting ``ssoConfig.ts`` + impersonation migrations:
  config coercion refusals, ``ImpersonationGrant`` lifecycle refusals.

A helper maps each error to its consumed code string (``auth_error_code``) so a
future middleware can emit the same wire codes the fleet already speaks.
"""

from __future__ import annotations

from typing import Optional

# ---------------------------------------------------------------------------
# Base + config


class SsoError(RuntimeError):
    """Base class for every SSO failure. Never instantiated directly."""


class SsoConfigError(SsoError, ValueError):
    """An SSO configuration is invalid (schema, protocol, idp prefix, ...)."""


class UnknownKeyError(SsoError, KeyError):
    """A keystore alias / key was not found or the key type is wrong."""


# ---------------------------------------------------------------------------
# Tenant + domain resolution (fail closed - no default tenant)


class UnknownTenantError(SsoError):
    """A host/tenant id maps to no tenant. There is no default tenant."""


class TenantNotConfiguredError(SsoError):
    """The tenant exists but has no SSO configuration (or SSO is disabled)."""


class IdpMappingConflictError(SsoError, ValueError):
    """One IdP tenant would map to more than one platform tenant."""


# ---------------------------------------------------------------------------
# SAML / OIDC assertion + token verification


class InvalidAssertionError(SsoError):
    """A SAML assertion failed parsing or a structural check."""


class SignatureVerificationError(SsoError):
    """A signature did not verify (tampered/forged message or wrong key)."""


class MissingSignatureError(SsoError):
    """A signature was required but absent (unsigned message, key required)."""


class UntrustedSignerError(SsoError):
    """The message was signed, but by a key/cert this tenant does not trust."""


class AssertionNotYetValidError(SsoError):
    """SAML assertion / OIDC token is not valid yet (NotBefore)."""


class AssertionExpiredError(SsoError):
    """SAML assertion / OIDC token has expired (NotOnOrAfter/exp)."""


class InvalidIdTokenError(SsoError):
    """An OIDC id_token failed verification (issuer/audience/nonce/format)."""


class InvalidNonceError(SsoError, ValueError):
    """The replay-protection nonce in a response did not match."""


class RelayStateError(SsoError):
    """The console auth-hub relay state JWT was missing/invalid/tampered."""


# ---------------------------------------------------------------------------
# Session tokens (scoped claims)


class InvalidSessionTokenError(SsoError):
    """A session token failed verification (signature, expiry, shape)."""


class SessionExpiredError(InvalidSessionTokenError):
    """The session token is past its expiry."""


class SessionRevokedError(SsoError):
    """The session was explicitly revoked (logout / impersonation revoke)."""


class MissingTenantClaimError(InvalidSessionTokenError):
    """A session token carried no tenant claim where one is required."""


class InvalidTenantClaimError(InvalidSessionTokenError):
    """A session token carried a tenant claim that is not a known tenant."""


class CrossTenantDenied(SsoError):
    """A session/principal from tenant A was used in tenant B (no fallback)."""


class LoginDeniedError(SsoError):
    """A successful IdP assertion could not be mapped to a tenant principal
    (missing identity/email attribute, or email domain not allowed)."""


# ---------------------------------------------------------------------------
# Impersonation (explicit grant + audit stamp)


class ImpersonationError(SsoError):
    """Base for impersonation failures."""


class ImpersonationNotGrantedError(ImpersonationError):
    """No active, explicit grant covers this operator->target impersonation."""


class GrantExpiredError(ImpersonationError):
    """The impersonation grant has expired."""


class GrantRevokedError(ImpersonationError):
    """The impersonation grant was revoked; its token is dead."""


class UnknownGrantError(ImpersonationError, KeyError):
    """No grant with this id exists."""


# ---------------------------------------------------------------------------
# Wire-code mapping (consumed vocabulary)


_ERROR_CODES: dict[type[SsoError], str] = {
    UnknownTenantError: "unknown_tenant",
    TenantNotConfiguredError: "sso_not_configured_for_tenant",
    InvalidSessionTokenError: "invalid_token",
    SessionExpiredError: "invalid_token",
    MissingTenantClaimError: "missing_tenant_claim",
    InvalidTenantClaimError: "tenant_mismatch",
    CrossTenantDenied: "tenant_mismatch",
    SignatureVerificationError: "invalid_token",
    MissingSignatureError: "invalid_token",
    UntrustedSignerError: "invalid_token",
    InvalidIdTokenError: "invalid_token",
    InvalidAssertionError: "invalid_token",
    AssertionExpiredError: "invalid_token",
    AssertionNotYetValidError: "invalid_token",
    RelayStateError: "invalid_state",
    LoginDeniedError: "profile_unavailable",
}


def auth_error_code(error: Exception) -> Optional[str]:
    """Map an SSO error to its consumed auth wire code (saas-rbac vocabulary).

    Returns the code for a known error type, ``"denied"`` for an SSO error
    without a dedicated code, and None for an unrelated exception.
    """
    for cls, code in _ERROR_CODES.items():
        if isinstance(error, cls):
            return code
    if isinstance(error, SsoError):
        return "denied"
    return None
