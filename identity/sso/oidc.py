"""OIDC OpenID Connect relying-party flows, modeled offline (issue #35).

Mirrors saas-rbac ``sso.ts``/``b2c.ts`` + shared-governance
``agent-oidc-config.schema.json``: a tenant's OIDC IdP is described by its
issuer, authorization endpoint (``idp_sso_url``), client id (``entity_id``)
and a signing key. Offline, ``verify_id_token`` verifies an id_token fixture
the same way the real RP would:

- signature (RS256 via the IdP's public key/cert, or HS256 via the shared
  client secret) with the algorithm pinned by the config - no algorithm
  confusion, ``none`` rejected;
- ``iss`` == the configured issuer, ``aud`` contains the configured client id
  (a token minted for another tenant's IdP or another audience is rejected);
- ``exp``/``iat``/``nbf`` within the clock-tolerance window;
- replay-protection ``nonce`` matches when one was issued.

Discovery metadata (``jwks_uri``, ``token_url``) is parsed but never fetched -
keys are injected from the keystore/fixtures.
"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlencode

from .errors import (
    InvalidIdTokenError,
    InvalidNonceError,
    SsoError,
)
from .jose import (
    jwt_unsign,
)
from .keystore import KeyStore
from .model import (
    ALG_HS256,
    ALG_RS256,
    OIDC,
    ResolvedPrincipal,
    SUBJECT_USER,
    TenantSsoConfig,
)

OIDC_SCOPE = "openid email profile"


def build_authorization_url(
    config: TenantSsoConfig,
    *,
    state: str,
    nonce: Optional[str] = None,
    code_challenge: Optional[str] = None,
    redirect_uri: str = "",
) -> str:
    """Build the IdP authorization URL for the OIDC authorization-code flow.

    ``state`` is the CSRF binding (carried end-to-end and verified on return);
    ``nonce`` binds the id_token to the login (replay protection);
    ``code_challenge`` enables PKCE when the RP uses the public/confidential
    client model.
    """
    if config.protocol != OIDC:
        raise SsoError("build_authorization_url requires an OIDC SSO config")
    params: dict[str, str] = {
        "response_type": "code",
        "client_id": config.client_id,
        "redirect_uri": redirect_uri or config.acs_url,
        "scope": OIDC_SCOPE,
        "state": state,
    }
    if nonce is not None:
        params["nonce"] = nonce
    if code_challenge is not None:
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = "S256"
    separator = "&" if "?" in config.idp_sso_url else "?"
    return f"{config.idp_sso_url}{separator}{urlencode(params)}"


def _config_signing(keystore: KeyStore, config: TenantSsoConfig) -> tuple[str, Any]:
    """(alg, key) pinned by the config for verifying this tenant's id_tokens."""
    if config.signing_kind == "secret":
        return ALG_HS256, keystore.get_secret(config.secret_alias)
    if config.cert_alias:
        return ALG_RS256, keystore.resolve_signing_public_key(config.cert_alias)
    raise SsoError(
        f"OIDC config for {config.tenant_id!r} has no signing key alias"
    )


def verify_id_token(
    token: str,
    config: TenantSsoConfig,
    keystore: KeyStore,
    *,
    now: Optional[int] = None,
    nonce: Optional[str] = None,
    extra_claims: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Verify an OIDC id_token and return its claims (fail closed).

    Every check below is a hard denial; there is no partial-accept path.
    """
    import time as _time

    now = int(now if now is not None else _time.time())
    alg, key = _config_signing(keystore, config)

    try:
        claims = jwt_unsign(token, alg=alg, key=key)
    except SsoError as exc:
        raise InvalidIdTokenError(
            f"id_token failed signature verification: {exc}"
        ) from exc

    tolerance = config.clock_tolerance_s

    if claims.get("iss") != config.issuer:
        raise InvalidIdTokenError(
            f"id_token issuer {claims.get('iss')!r} does not match the "
            f"configured issuer {config.issuer!r}"
        )
    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    if config.client_id not in audiences:
        raise InvalidIdTokenError(
            f"id_token audience {audiences!r} does not include the client id "
            f"{config.client_id!r}"
        )

    issued_at = claims.get("iat")
    if not isinstance(issued_at, int) or issued_at > now + tolerance:
        raise InvalidIdTokenError("id_token has no valid iat")
    expiry = claims.get("exp")
    if not isinstance(expiry, int) or expiry < now - tolerance:
        raise InvalidIdTokenError("id_token has no valid exp (expired?)")
    if "nbf" in claims:
        not_before = claims["nbf"]
        if not isinstance(not_before, int) or not_before > now + tolerance:
            raise InvalidIdTokenError("id_token is not valid yet (nbf)")

    if nonce is not None and claims.get("nonce") != nonce:
        raise InvalidNonceError("id_token nonce does not match the issued nonce")

    if extra_claims is not None:
        for key_name, expected in extra_claims.items():
            if claims.get(key_name) != expected:
                raise InvalidIdTokenError(
                    f"id_token claim {key_name!r} mismatch "
                    f"({claims.get(key_name)!r} != {expected!r})"
                )

    if not claims.get("sub"):
        raise InvalidIdTokenError("id_token carries no subject (sub)")
    return claims


def principal_from_id_token(
    claims: dict[str, Any],
    config: TenantSsoConfig,
    *,
    email: Optional[str] = None,
) -> ResolvedPrincipal:
    """Map verified id_token claims to a tenant principal (email -> user).

    The email claim defaults to OIDC's standard ``email`` and can be remapped
    via ``config.email_attribute`` (capital-underwriting ``emailClaim``).
    A verified token with no mappable email is a denial (never a partial
    principal) - callers that need NameID-style subjects must supply one.
    """
    from .errors import LoginDeniedError

    email_key = config.email_attribute or "email"
    identity_email = (email if email is not None else claims.get(email_key)) or ""
    identity_email = str(identity_email).strip().lower()
    if not identity_email:
        raise LoginDeniedError(
            f"id_token carries no {email_key!r} claim; cannot map to a tenant user"
        )
    return ResolvedPrincipal(
        tenant_id=config.tenant_id,
        idp_tenant_id=config.idp_tenant_id,
        provider_protocol=OIDC,
        subject_id=identity_email,
        subject_type=SUBJECT_USER,
        email=identity_email,
        name=str(claims.get("name") or claims.get(config.name_attribute) or ""),
        role=str(claims.get("role", "")),
        attributes=dict(claims),
        idp_subject=str(claims.get("sub", "")),
        groups=tuple(str(g) for g in claims.get("groups", ())),
    )


def discovery_endpoints(discovery: dict[str, Any]) -> dict[str, str]:
    """Validate + normalize an OIDC discovery document (never fetched offline).

    Returns the endpoint subset this model consumes: issuer,
    authorization_endpoint, token_endpoint, jwks_uri. Refuses a document whose
    issuer is absent or whose authorization endpoint is missing.
    """
    if not isinstance(discovery, dict):
        raise InvalidIdTokenError("discovery document is not an object")
    issuer = discovery.get("issuer")
    if not isinstance(issuer, str) or not issuer:
        raise InvalidIdTokenError("discovery document has no issuer")
    authorization = discovery.get("authorization_endpoint")
    if not isinstance(authorization, str) or not authorization:
        raise InvalidIdTokenError(
            "discovery document has no authorization_endpoint"
        )
    return {
        "issuer": issuer,
        "authorization_endpoint": authorization,
        "token_endpoint": str(discovery.get("token_endpoint") or ""),
        "jwks_uri": str(discovery.get("jwks_uri") or ""),
        "userinfo_endpoint": str(discovery.get("userinfo_endpoint") or ""),
    }
