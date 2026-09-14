# `integrations/paperclip/auth/` — cross-boundary auth for the paperclip seam

This package closes item **11** of the mismatch list in
[`docs/PAPERCLIP-ING-INTEGRATION.md`](../../../docs/PAPERCLIP-ING-INTEGRATION.md) §5
(“auth models do not meet natively”): upstream has agent keys/JWTs, a board
session cookie and a board token; the fleet had no HTTP caller identity at all.
The two models meet **once**, here, at the process boundary.

It is a subpackage of the canonical adapter module (`integrations/paperclip/`),
which is the only legitimate home for paperclip boundary code — see
[`../README.md`](../README.md) and the guard
[`scripts/check-paperclip-canonical-module.sh`](../../../scripts/check-paperclip-canonical-module.sh).
The mode decision is
[ADR-0013](../../../docs/decision-records/ADR-0013-paperclip-ing-integration.md);
the ownership boundary is
[ADR-0012](../../../docs/decision-records/ADR-0012-hermes-paperclip-boundary.md).

## The files

| File | Role |
|---|---|
| `jwt.py` | the stdlib-only HS256 primitive: sign, verify, and an unverified `peek_claims` used only to route a credential to its verifier |
| `registry.py` | agent identity **minted and verified from the fleet's own registry seeds**; permission authority is re-derived from the record, never stored in a token |
| `board.py` | human identity mapped onto the fleet's existing **board session path**, so an operator needs no third login |
| `runbridge.py` | the run-correlation **bridge**: upstream `X-Paperclip-Run-Id` ↔ the fleet's own `correlation_id`, single-use |
| `policy.py` | the closed boundary permission vocabulary and the two-gate (scope, then permission) shape |
| `guard.py` | the pipeline that runs one request: authN → scope → authZ → run correlation |
| `model.py`, `cli.py` | typed shapes and refusals; `mint-agent`, `mint-board`, `authorize`, `controls` |
| `tests/` | the offline suite (JWT, agent identity, board identity, run bridge, boundary pipeline) |

## Acceptance criteria, mapped

| Criterion | Where it is answered |
|---|---|
| Agent identity minted/verified from the fleet's own records, company scope in the claim, **no parallel identity store** | `registry.py` reads `registry/profiles/seeds/*.yaml` through `integrations.paperclip.mapping.iter_seed_profiles`; a subject that is not a registered agent is refused and an unregistered agent cannot be minted for |
| Human identity maps to the board token / session path (no third login) | `board.py` mints the board token from a `governance/isolation` `SessionIdentity` record (`SessionIdentity.to_json()` shape) and re-checks the session on verify |
| Run correlation bridged (`X-Paperclip-Run-Id` ↔ `correlation_id`) | `runbridge.py` joins the two and keeps both directions resolvable; the fleet value is the authoritative one ([`fleet/schema/message.schema.json`](../../../fleet/schema/message.schema.json) requires `correlation_id` on ack/result) |
| Negative controls first-class and proven | `cli.py controls` provokes every one, in process, with an ephemeral key; [`scripts/check-paperclip-auth.sh`](../../../scripts/check-paperclip-auth.sh) is the gate |
| No secret value written or echoed (GR-6) | no key material exists in this package; the signing key is injected from the environment (`PAPERCLIP_AUTH_KEY`) and a refusal never interpolates the credential it refused |

## The refusals (each provoked by name)

| Control | Refused as |
|---|---|
| expired token | `401 token_expired` |
| unknown identity (token for an unregistered agent / a forged signature) | `401 invalid_token` |
| token scoped to another company | `403 cross_tenant` |
| replayed `X-Paperclip-Run-Id` | `409 replayed_run_id` |
| missing `Authorization` header | `401 unauthorized` |
| authenticated but not allowed | `403 permission_denied` — **never 404** |

The last row is the point of the whole seam: a known caller denied a known route
is a denial, not a missing resource. The permission vocabulary is closed, so an
*undeclared* permission is a programming error (`ValueError`) rather than a 404 —
the code cannot produce a 404 where a 403 is due.

## The vocabulary is the fleet's, not a fork

The wire codes mirror [`identity/cpapi/errors.py`](../../../identity/cpapi/errors.py)
(`401 unauthorized` / `invalid_token` / `session_revoked`, `403 cross_tenant` /
`permission_denied`, `409 conflict`); this seam adds only `token_expired` and
`replayed_run_id`. The claim vocabulary (`iss`/`sub`/`aud`/`iat`/`exp`/`jti`/
`purpose`/`kind`/`company`/`session_id`) is legible to the same reader as a fleet
session token, and the permission gate has the two-gate shape of
[`identity/rbac/`](../../../identity/rbac/). Only the HS256 primitive is local —
the canonical module is stdlib-only by design, so it carries its own rather than
taking a dependency. That is the same ADR-0012 choice the module already made for
its YAML and JSON-Schema helpers: **map the vocabulary, do not couple the runtime**.

## Running it

```bash
bash scripts/check-paperclip-auth.sh                       # the gate (tri-state)
bash scripts/check-paperclip-auth.sh --no-controls         # structure only
python3 integrations/paperclip/auth/cli.py controls        # provoke every refusal
python3 integrations/paperclip/auth/cli.py mint-agent --agent paperclip
```

`controls` mints with an **ephemeral in-process key** when `PAPERCLIP_AUTH_KEY`
is unset, so the refusals can be proven with no secret material present at all.
