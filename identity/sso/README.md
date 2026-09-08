# identity/sso — Tenant identity + SSO (tenant-to-IdP mapping, SAML/OIDC)

The tenant-identity / SSO contract of the AI-agent-orchestration platform
(issue #35, work item 31, phase 6; parent EPIC-00 issue #4). Owner lane:
**identity** — this subtree is `identity/sso/**` only. Doctrine:
[`AGENTS.md`](../../AGENTS.md),
[`docs/EXECUTION-PLAN.md`](../../docs/EXECUTION-PLAN.md),
[`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md),
[`docs/GOLDEN-RULES.md`](../../docs/GOLDEN-RULES.md).

> **Security-sensitive lane (authN core).** Rigor is mandatory: everything
> fails closed, every gate has a negative test, and no real secret ever lives
> in this subtree. The whole model is **offline**: SAML/OIDC flows are
> implemented at the message/assertion-parsing level and exercised with
> fixtures; there are no real-IdP network calls and no IdP SDKs.

## What this is

```
tenant (the agent org) ── maps to ──> one identity provider (SAML or OIDC)
        │                                   ^
        │  custom-domain resolution          │ one IdP tenant per platform tenant
        ▼                                   │
   SSO login (SP/RP) ── assertion/id_token ──┘
        │  signature + issuer + audience + time verified
        │  attribute mapping email -> user, verified-domains policy
        ▼
   session token, scoped to EXACTLY that tenant (tenantId + subject + role)
        │
        ├─ use: refused in any other tenant (no cross-tenant fallback)
        ├─ logout / revoke: jti-keyed revocation
        └─ impersonation: explicit grant + audit stamp (enterprise support)
```

The acceptance criteria (from the issue body) are:

1. Tenant ↔ IdP-tenant mapping; per-tenant SSO config (SAML/OIDC);
   custom-domain resolution — `config.py`, `domains.py`, `store.py`.
2. Session token carries tenant (+agent/user +role) claims; the verifier
   rejects a missing/invalid tenant claim — `tokens.py`, `sessions.py`.
3. Console SSO model: auth-hub relay (PKCE in `state`), RS256 `os-session-token`
   + JWKS + allowlist — `sessions.py` console methods, `tokens.py`.
4. Impersonation with explicit grant + audit stamp (enterprise support path,
   harvested from capital-underwriting) — `impersonation.py`.

## Provenance (cannibalization)

Adapted to Python, offline, from the fleet's read-only sources (per the
cannibalization index in [`docs/CANNIBALIZATION.md`](../../docs/CANNIBALIZATION.md)):

- `saas-rbac` `services/frontend-api/src/auth/tenant-resolution.ts` +
  `tenant-mapping.ts` + `sso.ts` + `b2c.ts` + `token-verifier.ts` +
  `authenticate.ts` — Host→tenant resolution (fail closed, no default tenant,
  ignore `X-Forwarded-Host`), `TenantMapping` (internal tenant id + IdP-tenant
  id + domain/aliases + SSO config), `saml.`/`oidc.` IdP config-name prefix
  rule, token tenant-claim cross-check, `AUTH_ERRORS` wire codes (READY-TO-
  REUSE).
- `capital-underwriting` `apps/server/prisma/schema.prisma`
  (`Account.ssoConfig`, `verifiedDomains`, `customDomain`),
  `apps/server/src/lib/sso/ssoConfig.ts` (the single protocol-discriminated
  `StoredSsoConfig` column + coercion refusals), impersonation migrations
  (`ImpersonationGrant`, `AuditLog.impersonatedByUserId`) (READY-TO-REUSE).
- `shared-frontend` `docs/AUTH.md` + `auth/` — the console SSO model: auth-hub
  relay with PKCE inside a short-lived HS256 `state` JWT, RS256 session token
  with `purpose: os-session-token`, JWKS with RFC 7638 kid + rollover-retained
  keys, `ROOT_ADMIN_EMAILS` allowlist, HttpOnly-cookie semantics (READY-TO-
  REUSE as the frontend model).
- `shared-governance` `GLOBAL_STANDARDS/agent-identity.md` +
  `schemas/agent-identity-jwt.schema.json` + `schemas/agent-oidc-config.schema.
  json` — the JWT claim vocabulary (`iss`/`sub`/`aud`/`iat`/`exp`/`jti`) and
  OIDC token-issuance policy defaults (TTL 3600, max 86400, RS256) (PATTERN).

Nothing was copied verbatim; the TypeScript/Prisma shapes were ported to
Python with the same semantics, tightened around the fail-closed doctrine.

## Consumed contracts (field names frozen upstream)

This lane imports the vocabulary below and does **not** redefine it:

| Upstream | Consumed |
|---|---|
| `identity/onboarding` (issue #14) | `IdpTenantMapping` (tenant_id / idp_tenant_id / issuer — one IdP tenant maps to one platform tenant) |
| `identity/rbac` (issue #12) | `Org`-as-tenant, `Session.roles` snapshot, `SUBJECT_USER`/`SUBJECT_AGENT` subject kinds, two-gate authorization (this lane issues; rbac authorizes) |
| `registry/service` (issue #10) | scoped-claims session token shape (`tenantId`, `sub`, `role`, …), `CrossTenantDenied`, jti tokens, no cross-tenant fallback |
| `saas-rbac` auth | host resolution, `AUTH_ERRORS`, IdP config-name prefixes |
| `capital-underwriting` | `StoredSsoConfig` (protocol-discriminated single column), `ImpersonationGrant`, verified domains, audit stamp |
| `shared-frontend` | `os-session-token` purpose, RS256 + JWKS + allowlist console model |
| `shared-governance` | JWT claims + OIDC config/token policy vocab |

## Tree layout

```text
identity/sso/
├── README.md           # this contract doc
├── __init__.py         # public surface (import as identity.sso)
├── model.py            # TenantSsoConfig, ResolvedPrincipal, SsoSession,
│                       #   ImpersonationGrant, SsoAuditEvent + vocab constants
├── errors.py           # fail-closed error taxonomy + consumed auth wire codes
├── jose.py             # offline JOSE: HS256/RS256 JWT, JWKS, RFC 7638 kid,
│                       #   RSA key/cert helpers (cryptography when importable)
├── keystore.py         # alias -> key material (certs, RSA keys, secrets) —
│                       #   injected at runtime, nothing persisted
├── config.py           # per-tenant SSO config validation + registration
├── domains.py          # Host -> tenant resolution + email-domain helpers
├── store.py            # InMemoryStore (seam) + FileStore (JSON operator path)
├── saml.py             # SAML SP: AuthnRequest + assertion parse/verify
├── oidc.py             # OIDC RP: authorization URL + id_token verify + discovery
├── tokens.py           # session + console tokens, JWKS, relay state, allowlist
├── sessions.py         # SsoService orchestrator (login, isolation, logout,
│                       #   console model)
├── impersonation.py    # explicit impersonation grants + audit stamp
└── tests/              # pytest suite (77 tests incl. negatives)
    ├── conftest.py     # sys.path bootstrap + env fixture
    ├── _support.py     # offline env builder + signed-fixture generators
    ├── fixtures/       # committed signed SAML assertion + cert, OIDC id_token
    │                   #   + JWKS + discovery (public material only)
    ├── test_config_domains.py
    ├── test_saml.py
    ├── test_oidc.py
    ├── test_tokens_tenant_claim.py
    ├── test_sessions_isolation_logout.py
    ├── test_console_sso_token.py
    ├── test_impersonation.py
    └── test_fixtures.py
```

## The model

Pure frozen data types in `model.py`:

- **`TenantSsoConfig`** — one platform tenant's SSO config. Protocol
  discriminated (`saml` | `oidc`), mirroring capital's single-column model;
  `idp_config_name` must start `saml.` / `oidc.` (saas-rbac rule). Secrets
  never live here: the IdP certificate and the OIDC client secret are held in
  the keystore and referenced by alias (`cert_alias` / `secret_alias`).
  Carries the attribute mapping email→user (`email_attribute`) + optional
  `name_attribute` / `role_attribute`, verified-domains
  (`domain_restrictions`), per-protocol clock tolerance and token TTL.
- **`ResolvedPrincipal`** — the identity an assertion/id_token maps to inside
  one tenant (subject id = email for human principals).
- **`SsoSession`** — an issued session credential scoped to one tenant
  (registry #10 + rbac #12 vocabulary).
- **`ImpersonationGrant`** — a durable, addressable explicit impersonation
  grant (capital shape), whose `jti` binds the impersonated session token.
- **`SsoAuditEvent`** — append-only audit stamps; `operator_user_id` is the
  impersonation audit stamp (capital `impersonatedByUserId` mirror).

## Registration (AC #1)

`config.register_tenant_sso(store, config, primary_domain, aliases)`:

1. validates the config (protocol, `saml.`/`oidc.` prefix, required fields per
   protocol, single signing key for OIDC, domain restrictions);
2. enforces the **one-to-one tenant↔IdP mapping**: an IdP tenant already bound
   to another platform tenant is refused (`IdpMappingConflictError`), and a
   tenant cannot be re-bound to a second IdP tenant;
3. persists the config + the host route table (primary domain + aliases).

`domains.require_tenant_for_host` resolves a `Host` header to a tenant and
**fails closed** — an unknown host is `UnknownTenantError`; there is no default
tenant and `X-Forwarded-Host` is ignored (it is attacker-controlled).

## SSO flows (offline)

### SAML (AC #1; `saml.py` + `sessions.SsoService.complete_saml`)

SP-initiated: `start_saml(tenant_id)` builds an `AuthnRequest` (SP entity id,
ACS URL, IdP SSO destination) and the IdP redirect URL. Assertion handling
parses a `Response`/`Assertion` fixture and enforces, in order:

1. no DOCTYPE/ENTITY constructs (XXE guard);
2. `Issuer` matches the configured IdP entity;
3. the RSA-SHA256 signature verifies against the tenant's trusted IdP
   certificate (unsigned-when-key-required, forged, or untrusted-signer
   assertions are rejected; the presented cert, when embedded, must match the
   trusted cert);
4. `Conditions`: `NotBefore`/`NotOnOrAfter` with clock tolerance +
   `AudienceRestriction` contains the SP entity id;
5. the configured `email_attribute` maps to the tenant principal (email→user).

> **Offline signature model.** Assertions are signed over a *documented
> canonical serialization* (deterministic: local names, namespace URIs declared
> on the root in sorted order, attributes sorted, text preserved verbatim) of
> the assertion with the `Signature` element removed (enveloped semantics).
> Any tamper changes the digest and the signature fails. A production SAML
> stack would use a vetted XML-DSig implementation with exclusive C14N; this
> model exercises the same integrity guarantees at the message-parsing level.

### OIDC (AC #1; `oidc.py` + `SsoService.complete_oidc`)

`start_oidc(...)` builds the authorization URL (client id, redirect, scope,
`state`, `nonce`, PKCE). `verify_id_token` verifies the id_token fixture
offline: pinned algorithm (RS256 via the IdP public key/cert held in the
keystore, or HS256 via the client secret — no algorithm confusion, `none`
rejected), `iss` == configured issuer, `aud` contains the client id,
`exp`/`iat`/`nbf` within the clock tolerance, and replay-protection `nonce`
when issued. Discovery metadata is parsed, never fetched.

### Verified domains (email→user policy)

A **valid** assertion can still be denied at this boundary: when the tenant
has `domain_restrictions` (or registered domains), an identity whose email
domain is not allowed is refused (`LoginDeniedError`) — the
capital-underwriting verified-domains gate.

## Session issuance + isolation (AC #2)

`SsoService.complete_saml / complete_oidc` issue an HS256 session token scoped
to **the tenant the principal authenticated into** (never a caller-supplied
tenant). Claims (registry #10 / shared-governance vocab):

```json
{ "iss": "urn:agent-orchestrator:sso", "sub": "<email>",
  "aud": ["urn:agent-orchestrator:api"], "iat": <now>, "exp": <now+ttl>,
  "jti": "<id>", "purpose": "api-session", "tenantId": "<tenant>",
  "subjectType": "user", "role": "<role snapshot>", "email": "<email>" }
```

The verifier (`SsoService.verify_session`) refuses a token whose tenant claim
is missing or invalid, and refuses to *use* a tenant-A session in tenant B
(`CrossTenantDenied`) — there is no cross-tenant fallback. `logout` revokes the
session `jti`; a revoked session is refused even while unexpired
(`SessionRevokedError`).

## Console SSO model (AC #3)

`SsoService` console methods port the shared-frontend model (frontend model
only, offline):

1. `console_relay_state(callback)` — a short-lived HS256 `state` JWT carrying
   the backend callback + a PKCE `code_verifier` (auth-hub relay does not sign
   `state`, so the auth-gate does);
2. `complete_console_login(state, email, tenant)` — verifies the relay state,
   runs the `ROOT_ADMIN_EMAILS` allowlist (`allowlist_only` denies everyone
   else), and mints an **RS256** `os-session-token`;
3. `console_jwks_payload()` / `verify_console(token)` — the public JWKS
   (`kid` = RFC 7638 thumbprint; retained rollover keys stay in the trusted
   set) and kid-indexed, fail-closed verification that also rejects any token
   that is not the `os-session-token` purpose.

## Impersonation (AC #4)

`impersonation.py` (capital support path):

- `create_impersonation_grant(...)` — records an explicit grant (operator →
  target in one tenant, for a stated reason, TTL-bounded). Nothing is possible
  without it (`UnknownGrantError`).
- `issue_impersonated_session(...)` — issues the impersonated session only when
  an active grant covers the exact operator/tenant/target triple; the session
  token's `jti` equals the grant's `jti` and carries `operatorUserId` +
  `impersonationGrantId`.
- `revoke_impersonation(...)` — revokes the grant and kills the bound session
  `jti`.
- Every impersonation event in the audit log stamps the **real operator**
  (`operator_user_id`) alongside the impersonated subject (capital
  `AuditLog.impersonatedByUserId` semantics).

## Fail-closed guarantees (negative-tested)

- A user from tenant A can never authenticate into tenant B (issuer / audience
  / cert / host all reject) and a tenant-A session is refused in tenant B.
- A tampered or forged SAML assertion / OIDC id_token is rejected; unsigned
  assertions are rejected when a certificate is configured; `none`/algorithm-
  confusion tokens are rejected.
- An expired / not-yet-valid assertion or token is rejected; a mismatched
  nonce is rejected; an out-of-audience assertion is rejected.
- A token without a (valid) tenant claim is rejected.
- A session whose `jti` was revoked (logout or impersonation revoke) is
  refused even while unexpired.
- Impersonation without an explicit, active grant is refused; revocation kills
  the session; every impersonation audit event stamps the operator.
- SSO is only usable for a tenant that has a valid, enabled config; an unknown
  host is never a login.

## Usage

```python
import sys
sys.path.insert(0, ".")                    # repo root (identity/ is a PEP-420
from identity.sso.config import register_tenant_sso
from identity.sso.model import TenantSsoConfig
from identity.sso.keystore import KeyStore
from identity.sso.store import InMemoryStore
from identity.sso.sessions import SsoService

store = InMemoryStore()
keystore = KeyStore()
# ... load the tenant's IdP certificate into keystore (runtime/env injection)
register_tenant_sso(
    store,
    TenantSsoConfig(
        protocol="saml", idp_config_name="saml.acme", tenant_id="acme",
        idp_tenant_id="idp-acme", entity_id="urn:acme:sp",
        acs_url="https://acme.example.com/acs",
        issuer="https://idp.acme.example.com",
        idp_sso_url="https://idp.acme.example.com/sso",
        cert_alias="cert:acme-saml", email_attribute="email",
    ),
    primary_domain="acme.example.com",
)

svc = SsoService(store, keystore, session_hmac_key=b"<32-byte-hmac-key>")
# host -> tenant -> SAML -> session
session = svc.complete_saml("acme.example.com", signed_assertion_bytes)
claims = svc.verify_session(session.token, expected_tenant="acme")
```

Keys and secrets are injected from env/secrets managers at runtime — this
subtree never stores, logs, or commits private key material (see
`keystore.py`). Test fixtures carry public material only (certificates, JWKS).

## Verification

```bash
python3 -m pytest identity/sso/tests -q        # 77 passed (incl. negatives)
make -C <worktree> verify                      # repo gate of record (must stay
                                               # green - this lane adds no files
                                               # outside identity/sso/)
```

Note: per the one-issue-one-lane doctrine this lane only *adds* files under
`identity/sso/`, so the suite is **not yet registered** in
`scripts/pytest-suites.txt` (a foundation/QA-owned file). Until a later pass
registers it, the drift check prints a non-fatal WARN (`make verify`/`make
gate` stay green); the suite is exercised here directly and by whoever
registers it.
