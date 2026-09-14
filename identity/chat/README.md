# `identity/chat` — scoped chat identity and tenant isolation (issue #505)

The chat surface's identity layer for EPIC #500. It mints a credential scoped
to exactly one `(tenant, agent, conversation)` and structurally isolates that
conversation's memory — while consuming, not forking, every merged pillar it
touches.

## The one design decision worth reading first

ADR-0023 fixes the front door: the auth gate's RS256 `os-session-token` is the
only way in, and a client's own user table is advisory, never authoritative.

**No new token format and no new verifier.** A chat credential *is* the
gateway's session credential — minted with `gateway.mcp.authn.mint_session` and
`session_to_token`, verified with `gateway.mcp.authn.verify_token`, unchanged.
The conversation scope therefore has to ride a claim the existing verifier
already returns, and it rides `aud`:

```
aud = ["control-plane", "chat:conversation:conv-1"]
```

Every other standard claim is already spoken for: `sub` is the external
principal, `jti` is the revocation handle, `tenantId`/`agentId` are the tenant
and agent. A bespoke `conversationId` claim was rejected on purpose —
`SessionIdentity.from_claims` ignores unknown keys, so such a token would still
*verify* while the conversation scope silently vanished. That is precisely the
false-green failure this module exists to avoid.

What `identity/chat` adds on top of the merged verifier is the scope assertion
only: tenant, agent, and the one conversation the token was minted for.

## Modules

| File | Consumes | Adds |
| --- | --- | --- |
| [`errors.py`](errors.py) | `identity.cpapi.errors` | The refusal taxonomy; `as_api_error()` converts onto the control plane's wire vocabulary |
| [`credential.py`](credential.py) | `gateway.mcp.authn`, `gateway.mcp.model` | Mint/verify a credential scoped to `(tenant, agent, conversation)`; scope assertions |
| [`binding.py`](binding.py) | — | The declared external-identity → tenant map ([`identity-map.json`](identity-map.json)) |
| [`frontdoor.py`](frontdoor.py) | `identity.sso.tokens` | The auth-gate identity, consumed exactly as `portal/server/sso.py` consumes it |
| [`isolation.py`](isolation.py) | `engine.memory.model`, `engine.memory.store` | The chat path onto the scoped store, plus crossing counters |
| [`approvals.py`](approvals.py) | `identity.cpapi.approvals` | Proposals routed to the approval gate; the chat surface's only side effect |

## The trust split, in one place

| Input | Authority |
| --- | --- |
| `os-session-token` (cookie) | RS256 auth-gate token, verified offline against the published JWKS (fail closed) |
| `X-Forwarded-Email` | The reverse proxy's authenticated client identity (the only trusted identity header) |
| `X-AO-Chat-Client` | Which client integration is calling — declared, never assumed |
| body / query `tenantId`, `tenant` | **Never** a source of authority. A foreign value is a refusal; the matching value changes nothing |
| body / query `conversationId` | The resource being addressed; the credential's scope binds it and `isolation` enforces the container |
| token `role` claim, body `role` | Discarded. The role comes from *our* allowlist and *our* declared map |

## Isolation contract

A conversation's memory lives in exactly one `engine/memory` container, named by
the merged engine vocabulary rather than formatted here:

```
session:<tenant>:<agent>:<conversation>
```

`ChatIsolation` refuses a request that names a different container, and
delegates the read/write to `engine.memory` under the credential's
`(tenant, agent, conversation)` so the store's own cover check runs underneath.
An `engine.memory.model.MemoryIsolationError` is re-raised **chained**
(`__cause__`) rather than swallowed — the chat path cannot bypass the engine's
isolation, only add a counting layer in front of it. Two counters make that
auditable: `cross_scope_denials` (refusals this layer issued) and
`engine_cross_scope_violations` (read back from `MemoryStore.stats()`).

## Approvals

A turn that proposes an action does not perform it. The proposal goes to
`identity.cpapi`'s approval gate; the gate either reports an active approved
authorization for exactly that `(action, resource)` or records an idempotent
pending request and the chat surface returns the control plane's
`approval_required` outcome. `ChatApprovalRouter` holds no approver — approval
authority belongs to the control-plane surface — and `ROUTER_PUBLIC_SURFACE`
names its entire public call surface, so the absence of a direct write path is
checked by a test rather than asserted in prose.

Note: `identity.cpapi.errors.approval_required` maps to HTTP **503** with code
`approval_required` in the merged control plane. The chat surface returns that
object verbatim rather than introducing a second status for the same condition.

## Fail-closed defaults

No anonymous mode. No tenant from the request body. No cross-tenant fallback.
No impersonation shortcut. No credential without a signing key (there is no
default key and no ephemeral fallback) and without a revocation store (there is
no path that verifies without consulting the merged jti deny list). No secret
in a tracked file or a request body.

## Tests and gate

```bash
python3 -m pytest identity/chat -q -p no:cacheprovider
bash scripts/check-chat-identity.sh
```

`tests/conftest.py` puts the repo root at the front of `sys.path`; there is no
`tests/__init__.py` and no `__init__.py` in `identity/` (house convention), so
tests import the fully-qualified path (`from identity.chat.credential import
...`). Every refusal test is paired with the control that proves the same call
succeeds for the correct scope, so a module that refused everything could not
pass.

`scripts/check-chat-identity.sh` re-runs the suite and then drives its own
refusal probes against the real modules, counting refusals and controls
separately. Its exit contract is `0` OK / `1` NOT-OK / `2` CANNOT-ASSESS.
