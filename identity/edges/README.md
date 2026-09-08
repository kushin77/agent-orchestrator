# identity/edges — Public API + proxy allowlist boundary (authn never authz)

The public front door of the AI-agent-orchestration platform (issue #37, work
item 33, phase 6; parent EPIC-00 issue #4). Owner lane: **identity** — this
subtree is `identity/edges/**` only. Doctrine:
[`AGENTS.md`](../../AGENTS.md),
[`docs/EXECUTION-PLAN.md`](../../docs/EXECUTION-PLAN.md),
[`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md),
[`docs/GOLDEN-RULES.md`](../../docs/GOLDEN-RULES.md).

> **Security-sensitive lane (authn vs authz separation).** The core doctrine
> here is **authn never authz**: the PUBLIC edge authenticates and forwards
> identity + scopes, but NEVER makes authorization decisions itself —
> authorization happens inside, after scope resolution, in the downstream
> backend (#38 REST server composing `identity/rbac` issue #12). Rigor is
> mandatory: everything fails closed, every gate has negative tests, and the
> whole model is **offline** (the edge is a gateway/proxy abstraction + an
> allowlist boundary model, testable without a real HTTP server — the real
> REST server arrives in issue #38). No npm/node; stdlib + PyYAML only.

## What this is

```
client ──> PUBLIC EDGE (identity/edges) ──> backend (#38 REST server,
                                              internal-only)
            1. ALLOWLIST   explicit public route+method allowlist; unknown
                           routes / traversal rejected (fail closed) — a
                           *publication* decision, never authorization
            2. AUTHN       verify the session token (issue #35 verify_session
                           semantics, injected); missing/invalid -> 401
            3. FORWARD     build (never copy) the outbound request: verified
                           identity + role snapshot to the backend; the
                           backend's status+body pass back UNTOUCHED
                           (a backend 403 stays a 403)
```

The edge decides **whether a route is publicly reachable** (allowlist) and
**who is calling** (authN). It never decides **what the caller may do** —
that is the backend's answer, after scope resolution, using
`identity/rbac` (issue #12). The backend does not trust the front door for
authorization by design; the front door is not trusted for authorization
either (saas-rbac `docs/ARCHITECTURE.md` split: "frontend-api authenticates;
it never authorizes").

## Acceptance criteria (issue #37)

| Criterion | Where |
|---|---|
| Public API surface `POST /v1/agents/:id/tasks`, `GET /v1/tenants/me/usage`, audit export, admin endpoints — all behind the allowlist proxy | [`allowlist.py`](allowlist.py) + [`config/public-routes.yaml`](config/public-routes.yaml) (the shipped default table) |
| Front door authenticates; backend authorizes (no trusting the front door) | [`authn.py`](authn.py) (authN) + [`edge.py`](edge.py) + the structural/behavioral guarantee in [`tests/test_structural.py`](tests/test_structural.py) |
| Internal routes are unreachable publicly unless explicitly allowlisted | [`allowlist.py`](allowlist.py) (allowlist, not denylist) + negatives in [`tests/test_allowlist.py`](tests/test_allowlist.py) |
| Versioned, envelope-standardized responses (capital middleware pattern) | [`model.py`](model.py) `EdgeResponse` + [`envelope.py`](envelope.py); `/v1/...` versioned public paths |
| Proxy passthrough abstraction (offline-testable; #38 is the real consumer) | [`edge.py`](edge.py) `Backend` seam + [`tests/`](tests/) |

## Provenance (cannibalization; GR-10)

Adapted to Python, offline, from the read-only fleet mirrors under
`.research/` (each issue-listed source was verified on disk before reuse; the
cannibalization index lives in [`docs/CANNIBALIZATION.md`](../../docs/CANNIBALIZATION.md)):

| Source | Pattern adapted | Where it landed |
|---|---|---|
| `saas-rbac` `services/frontend-api/src/proxy.ts` | explicit route+method allowlist (`PROXYABLE_SECTIONS`, `PROXY_METHODS`), path-traversal guard (`parseProxyPath`: reject empty/`.`/`..`/encoded-slash/malformed-encoding, fold into `unknown_api_route`), build-don't-copy outbound request (only the trace header forwarded; client cannot smuggle identity), status/body pass-through untouched | [`paths.py`](paths.py) traversal guard; [`allowlist.py`](allowlist.py); [`forward.py`](forward.py); [`edge.py`](edge.py) |
| `saas-rbac` `docs/ARCHITECTURE.md` | authn (frontend/edge) ≠ authz (backend) split; backend internal-only; IAM-invoked downstream | the whole lane's separation doctrine (README §authn never authz) |
| `hermes-agents` `src/hermes_agent/api/{capabilities,router,tiering}.py` | `/api/v1/...` versioned REST shape + success/error envelope | `/v1` public paths + the versioned [`envelope.py`](envelope.py) |
| `shared-services` `automation/govctl/*` + `services/mcp-hub/server/mcp-server.ts` | JWT-authenticated gateway posture, request metadata + correlation id | authN-at-the-edge seam + `requestId` correlation envelope |
| `CMR` `registry/events/` (event bus shape; in-repo mirror at `registry/events`) | append-only event/log conventions for downstream audit | consumed downstream (audit export route); the edge itself only relays |

Nothing was copied verbatim; the TypeScript shapes were ported to Python with
the same semantics, tightened around the fail-closed doctrine. Note the issue
body names `frontend-api/src/proxy.ts`; the verified on-disk path is
`saas-rbac/services/frontend-api/src/proxy.ts` (the `services/` prefix is
present in the mirror).

## Consumed contracts (field names frozen upstream)

This lane imports the vocabulary below and does **not** redefine it:

| Upstream | Consumed |
|---|---|
| `identity/sso` (issue #35) | session-token claim keys (`tenantId`/`sub`/`subjectType`/`role`/`email`/`purpose`/`jti`); `SsoService.verify_session(token, expected_tenant=None, now=None) -> claims` semantics — injected as the [`authn.py`](authn.py) verifier seam (`sso_verifier`), never reimplemented |
| `identity/rbac` (issue #12) | subject kinds `user`/`agent`; the two-gate authorization flow (scope then permission) — the **downstream** authorizes; the edge holds the role keys as a forwarded *snapshot* only and contains no rbac import/call (structural test) |
| `registry/service` (issue #10) | scoped-claims session identity (`tenantId`/`sub`/`role`); role snapshot is context, never a grant |
| `gateway/proxy` (issue #16) | thin-handler envelope semantics (`{status, result, record}`) + the `POST /v1/agents/{agentId}/tasks` surface the edge allowlists |
| `saas-rbac` | allowlist + traversal-guard + build-don't-copy + pass-through doctrine |
| `capital-underwriting` middleware | error-handler envelope (`code`/`message`/`details`) — adapted as the edge's closed `error.code` envelope |

## Tree layout

```text
identity/edges/
├── README.md                   # this contract doc
├── __init__.py                 # public surface (import as identity.edges)
├── model.py                    # vocab: routes, forwarded identity/request,
│                               #   versioned envelope + closed error codes
├── paths.py                    # path-traversal guard + route-template match
├── allowlist.py                # explicit public route allowlist + matcher +
│                               #   default table + YAML loader/parity
├── authn.py                    # injected session-verifier seam (authN only)
├── forward.py                  # build-don't-copy outbound request builder
├── envelope.py                 # versioned, envelope-standardized responses
├── edge.py                     # PublicEdge orchestrator (authN + allowlist +
│                               #   forward) + Backend passthrough seam
├── cli.py                      # offline demo/evidence CLI
├── config/
│   └── public-routes.yaml      # declarative default allowlist (YAML/code parity)
└── tests/                      # pytest suite (97 tests incl. negatives)
    ├── conftest.py             # sys.path bootstrap + shared fixtures
    ├── _support.py             # offline stub verifiers + recording backend
    ├── test_allowlist.py       # allowlist + traversal + method negatives
    ├── test_authn.py           # authN negatives + real #35 SsoService integration
    ├── test_forwarding.py      # build-don't-copy + authz passthrough
    ├── test_structural.py      # AST proof the edge never authorizes
    ├── test_envelope.py        # versioned envelope contract
    └── test_config.py          # YAML/code parity + validation
```

## The model

Pure frozen data types in [`model.py`](model.py):

- **`EdgeRoute`** — one publicly reachable route on the allowlist:
  `api_path` (versioned public template), `method`, `backend_path`
  (downstream template; may reference the matched path params and the
  reserved `{tenantId}` filled from *verified* claims), `authenticated`,
  optional `expected_tenant` pin. A route becomes public **only** when added
  here on purpose.
- **`ForwardedIdentity`** — who the caller is as the edge sees them
  (`tenant_id`, `subject_id`, `subject_type`, `email`), derived strictly from
  verified claims.
- **`ForwardedRequest`** — the built (not copied) outbound request: method,
  rendered backend path, query, body, allowlisted trace header, the
  `identity` and the `role_snapshot` (context, never a grant).
- **`EdgeResponse`** — the versioned envelope: `apiVersion` + `requestId` +
  `status` always, plus either the downstream `body` verbatim (pass-through)
  or a closed edge `error.code`.
- Closed error codes: `unauthenticated` (401), `unknown_route` (404 — also
  the folded traversal/malformed-path rejection), `method_not_allowed`
  (405), `backend_unavailable` (502, reason never leaked).

### 1. Allowlist boundary (AC #1/#3)

[`allowlist.py`](allowlist.py) `PublicRoutes.match(method, path)`:

1. the request path is split into plain forward segments by
   [`paths.py`](paths.py) — the traversal guard.  Anything that is not a
   plain forward path (empty segments, `.`/`..`, percent-encoded forms,
   an encoded slash inside a segment, malformed percent-encoding) is refused
   and folded into `unknown_route` (a client cannot distinguish "route exists
   but traversal blocked" from "route does not exist");
2. the path is matched 1:1 against the allowlisted route templates
   (no wildcards — the allowlist is explicit); an unknown path is
   `unknown_route`;
3. the HTTP method must be on the route's method allowlist, else
   `method_not_allowed`.

The allowlist is **additive by construction**: adding an internal-only route
to the backend never publishes it — a request to `/internal/...` (or any
other unlisted path) is rejected before any backend call (negative-tested in
`test_allowlist.py`). The shipped default table (`config/public-routes.yaml`,
mirrored in code) publishes the AC #1 surface: `POST /v1/agents/{agentId}/tasks`
(tasks), `GET /v1/tenants/me/usage` (usage), `GET /v1/tenants/me/audit/export`
(audit export), `GET /v1/admin/agents` + `POST /v1/admin/tenants` (admin),
and the single unauthenticated `GET /healthz`.

### 2. Authentication at the edge (AC #2; [`authn.py`](authn.py))

The edge authenticates through an injected verifier seam whose contract is
issue #35's `SsoService.verify_session` (signature + expiry + revocation +
optional `expected_tenant` no-cross-tenant check). `sso_verifier(service)`
adapts a real `SsoService`; tests inject both a stub and the real offline
`SsoService`. `verify_caller(...)` returns the caller identity + role
snapshot or a fail-closed `unauthenticated` rejection — a missing, malformed,
invalid, expired, revoked or tenant-mismatched token is a 401 and the
request never reaches the backend. The unauthenticated `/healthz` route is
the only one that skips authN.

### 3. Forwarding — built, never copied (AC #2; [`forward.py`](forward.py))

The outbound request is **built**, not copied: method/path/query/body come
from the allowlist route + validated request, and headers are set here — a
client can never smuggle `authorization`, `x-tenant-id` or any identity
header into the internal call (only the allowlisted `x-request-id` trace
header is forwarded, and the backend treats it as an opaque log field). The
verified identity and role snapshot ride as dedicated fields (the offline
transport model of the internal IAM-injected identity). The `me` routes
render `{tenantId}` from the *verified session claims* — the public path
never names a real tenant, so `/v1/tenants/globex/usage` is not even
allowlisted.

### 4. Versioned envelope (AC #4; [`envelope.py`](envelope.py))

Every response is the uniform versioned envelope (`apiVersion: v1` +
`requestId` + `status`). When the edge called the backend, the downstream
`status` and `body` pass through **untouched** — a backend 403 (authorization
denial) stays a 403 with its body intact; the edge never rewrites a decision.
Edge-origin rejections (no downstream call) carry a closed `error.code`.

## How authn never authz is guaranteed

1. **Structural (AST).** [`tests/test_structural.py`](tests/test_structural.py)
   parses every runtime module under `identity/edges` and asserts there is no
   import of `rbac`/`identity.rbac` and no call/attribute reference to the
   authorization vocabulary (`authorize`, `resolve_scope`, `guard`,
   `guard_session`, `start_agent_session`, `authorization_denied_payload`,
   `Decision`, ...). The edge *may* authenticate and *may* allowlist; it may
   not authorize. This check fails on real code, not prose.
2. **Behavioral.** A request the backend denies (403) is relayed verbatim;
   the edge never turns a denial into a 200 and never derives an allow/deny
   from the identity or role snapshot it forwards (an admin route called by a
   non-admin is still forwarded; the *backend's* decision is what comes back).
3. **The role snapshot is context, never a grant.** The edge forwards the
   session's role keys for downstream audit/context; the downstream
   re-resolves live bindings on every call (rbac #12 no-caching doctrine), so
   a revocation takes effect downstream immediately.

## Fail-closed guarantees (negative-tested)

- An unknown public path, an internal-only route, a raw/encoded/double-encoded
  traversal, an empty segment, a trailing slash, or a malformed percent-escape
  is rejected (404) **before any backend call**.
- A non-allowlisted HTTP method on a known path is rejected (405).
- A missing/malformed/invalid/expired/revoked/cross-tenant session token is
  rejected (401) — including against the **real** offline `identity/sso`
  `SsoService` (integration tests), and the 401 is opaque (no reason leak).
- An unauthenticated route is the only way past authN, and only when the
  allowlist says so.
- A backend transport failure is a 502 whose reason is never leaked.
- A backend authorization denial (403/429/...) passes through untouched.
- The allowlist itself is validated at construction: duplicates,
  non-allowlisted methods, malformed templates, and empty tables are refused.

## Usage

All commands run from the repo root; everything is offline (stdlib + PyYAML).

```python
import sys
sys.path.insert(0, ".")                    # repo root (PEP-420 identity.*)
from identity.edges.allowlist import default_public_routes
from identity.edges.authn import sso_verifier
from identity.edges.edge import PublicEdge
from identity.edges.model import EdgeRequest

# The verifier is the real issue #35 SsoService (offline), injected.
edge = PublicEdge(
    default_public_routes(),
    verifier=sso_verifier(my_offline_sso_service),   # verify_session semantics
    backend=my_downstream_backend,                    # the #38 REST server seam
)

response = edge.handle(EdgeRequest(method="POST", path="/v1/agents/a1/tasks",
                                   headers={"authorization": "Bearer <session>"},
                                   body={"taskType": "classify-route"}))
print(response.to_dict())   # {"apiVersion": "v1", "requestId": "req_...",
                            #  "status": 200, "body": <downstream body verbatim>}
```

CLI (offline demo + evidence):

```bash
python3 identity/edges/cli.py routes      # print the public allowlist
python3 identity/edges/cli.py probe --method POST --path /v1/agents/a1/tasks
python3 identity/edges/cli.py demo        # end-to-end walkthrough incl. negatives
```

## Verification

```bash
python3 -m pytest identity/edges/tests -q -p no:cacheprovider   # 97 passed
python3 -m pyflakes identity/edges                               # clean
python3 -m py_compile identity/edges/*.py identity/edges/tests/*.py
make verify                                                      # repo gate stays
                                                                 # green (this lane
                                                                 # adds files only
                                                                 # under identity/edges/)
```

Per the one-issue-one-lane doctrine this lane only *adds* files under
`identity/edges/`, so the suite is **not yet registered** in
`scripts/pytest-suites.txt` (a foundation/QA-owned file). Until a later pass
registers it, the drift check prints a non-fatal WARN (`make verify` stays
green — the same posture as `identity/sso`); the suite is exercised here
directly and by whoever registers it.
