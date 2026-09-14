# portal — Admin/tenant control-plane console (design tokens + SSO)

> Owner lane: **portal** (issue `kushin77/agent-orchestrator#39`, work item 35,
> phase 7; parent EPIC-00 issue #4). Doctrine:
> [`AGENTS.md`](../AGENTS.md),
> [`docs/EXECUTION-PLAN.md`](../docs/EXECUTION-PLAN.md),
> [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md),
> [`docs/GOLDEN-RULES.md`](../docs/GOLDEN-RULES.md),
> [`docs/CANNIBALIZATION.md`](../docs/CANNIBALIZATION.md).

This subtree is the **admin/tenant control-plane portal**: a single console
(super-admin multi-tenant view + per-tenant org view) built on the harvested
design-token system and console SSO. Tenants see agents, personas, prompts,
budgets, usage, audit and approvals — and flip **policy controls** that are
bound, server-side, to policy state (never UI-only).

Everything runs **fully offline**: the console is a self-contained static app
(design-token CSS/JSON twins + vanilla JS views) served by a small
Python-stdlib HTTP layer that also implements the console routes + SSO session
check. No npm, no tsc, no node_modules, no network. The pytest suite drives the
server handler directly plus static-asset checks.

## Run it (offline)

```bash
# from the repo root
python3 -m portal.server.main --port 8787
# open http://127.0.0.1:8787/
```

The console has **no login of its own**: an unauthenticated visitor is
redirected to the shared-frontend OS auth gate (`/auth/login`), and a console
session exists only once the portal has verified the auth-gate RS256
`os-session-token` offline against the gate's published JWKS. Configure the
mirror + allowlist before starting the server (with neither set, the console
refuses every session — fail closed):

```bash
PORTAL_AUTH_GATE_JWKS_FILE=/etc/ao/auth-gate-jwks.json   # or PORTAL_AUTH_GATE_JWKS=<inline JSON>
ROOT_ADMIN_EMAILS=root@platform.example.com             # comma-separated super-admin allowlist
```

Offline seed directory (`server/state.py`) — the **org bindings** those
identities are authorized with; sign-in itself always happens at the gate:

| Email | Console role | Org role | Scope |
|---|---|---|---|
| `root@platform.example.com` | `root_admin` (allowlist) | — | super-admin — every tenant |
| `alice@acme.example.com` | user | `owner` (acme) | tenant acme, full control incl. policy toggles |
| `bob@acme.example.com` | user | `admin` (acme) | tenant acme, read-only on controls |
| `erin@acme.example.com` | user | `agent-operator` (acme) | tenant acme, run/read only |
| `carol@globex.example.com` | user | `owner` (globex) | tenant globex |
| `dan@initech.example.com` | user | `owner` (initech) | tenant initech |

Verify with pytest (no network):

```bash
python3 -m pytest portal/tests -q -p no:cacheprovider
make -C <repo-root> verify     # repo gate stays green
```

## Acceptance criteria → where

| Acceptance criterion | Where |
|---|---|
| Console shell with design tokens (CSS+JSON twins + provenance), dark mode, per-frame CSS no-cascade | `static/design-tokens/tokens.css` + `tokens.json` + `PROVENANCE.md`, `static/css/console.css`, `static/views/shell.html`; each view frame links the twins itself |
| Views: Tenant overview · Agents (org tree, profiles) · Personas · Prompts (versions + FP/FN) · Policies/Controls (toggle, default OFF) · Budgets/Usage · Audit (verify chain) · Approvals feed (real-time) | `static/views/{overview,agents,personas,prompts,policies,budgets,usage,audit,approvals,tenants}.html` over the endpoints in `server/app.py` |
| SSO via the shared-frontend auth gate (one front door) + RBAC super-admin vs tenant-admin | `server/sso.py` (verifies the RS256 `os-session-token` offline against the auth-gate JWKS mirror, `purpose: os-session-token`, fail closed — issues nothing), `server/authz.py` (issue #12 role pack); no portal login form/route |
| Every control toggle maps to a policy control (no UI-only state) | `server/controls.py` (mapping table + `PolicyStateStore` + `PolicyEnforcer`), `catalog/policy-controls.yaml`; proof in `tests/test_controls_mapping.py` |

## Layout

```text
portal/
├── README.md                    # this contract doc
├── catalog/
│   └── policy-controls.yaml     # portal policy controls (default OFF)
├── server/                      # offline backend (Python stdlib)
│   ├── __init__.py
│   ├── app.py                   # ConsoleApplication: route table + pipeline
│   ├── authz.py                 # RBAC super-admin vs tenant-admin (issue #12)
│   ├── auditlog.py              # tamper-evident per-tenant audit chain
│   ├── catalog.py               # read-model projections for the views
│   ├── controls.py              # control→policy mapping + policy state/enforcer
│   ├── httpd.py                 # http.server binding (no sockets in tests)
│   ├── main.py                  # `python3 -m portal.server.main`
│   ├── sso.py                   # auth-gate session verifier (JWKS mirror, fail closed)
│   └── state.py                 # seeded offline demo state
├── static/
│   ├── css/console.css          # component/chrome styles (token-driven)
│   ├── design-tokens/           # harvested token system (CSS + JSON twins)
│   ├── js/api.js + console.js   # frame API + shell chrome
│   └── views/*.html             # shell + auth-gate redirect + the 9 views
└── tests/                       # offline pytest suite (90 tests incl. assets)
```

## Consumed contracts (frozen vocabularies — never redefined)

| Upstream | Consumed |
|---|---|
| `identity/sso` (issue #35) | console SSO: relay `state` JWT, RS256 `os-session-token` (`purpose: os-session-token`), RFC 7638 kid + JWKS, ROOT_ADMIN allowlist (`root_admin`), HttpOnly cookie semantics; a revoked console session is refused while unexpired |
| `identity/rbac` (issue #12) | Org-as-tenant, platform role pack `owner/admin/team-admin/agent-operator/member/viewer` permission vocabulary; two-gate (scope then permission) authorization |
| `identity/cpapi` (issue #38) | envelope `{ok,status,requestId,data,error}`, approval-gated destructive ops (`202 approval_required` → approve executes), endpoint/permission surface |
| `guardrails/policy` (issue #26) | the default-OFF controls registry (`controls.yaml`: `model-call-budget`, `tool-use-guard`, `data-egress-guard`) the Policies view toggles are bound to |
| `telemetry/ledger` (issue #31) | audit record vocabulary (`seq/ts/tenantId/actor/action/prevHash/hash`) + verify-chain semantics |
| `telemetry/budgets` (issue #34) | budget/quota shapes + the pause rail a portal control mirrors |
| `registry/service`, `registry/personas`, `registry/prompts` | agent statuses (`registered/active/paused/retired`), persona cards, prompt modules + per-version FP/FN feedback metrics |

## Controls → policy (no UI-only state)

`server/controls.py` builds the catalog from:

1. the **guardrails controls registry** (read-only, issue #26) — the three
   platform controls (`model-call-budget`, `tool-use-guard`,
   `data-egress-guard`), each shipping `enabled: false`;
2. the **portal catalog** (`catalog/policy-controls.yaml`) — tenant-scale
   controls also default OFF (`pause-rail`, `feedback-gate`), each with an
   `implemented_by` record of the pillar rail it mirrors.

`CONTROL_POLICY_MAP` is the server-side mapping table: every control id the UI
can offer has a registered policy control. `PolicyStateStore` holds the
per-tenant state (default OFF, AO-GR-6); a toggle POST writes that store and
appends an audit record; `PolicyEnforcer.evaluate(tenant, action)` turns the
state into an enforcement decision, so flipping a control genuinely changes
policy state (proof: `tests/test_controls_mapping.py` — a second actor reads
the same server state and the gated action's decision flips to `block`).
Control state is tenant-isolated and `policy:manage` is refused for every role
except `owner`/super-admin.

## The console SSO + RBAC pipeline

```
browser → /views/login.html → relay-state → POST login (email+tenant+state)
   └─ identity/sso SsoService.complete_console_login   (real issue #35 code)
        └─ allowlist → root_admin | user        + HttpOnly os-session-token
console shell → /api/console/me → tenant switcher / frame host
per /api/*: authN (verify_console, kid-indexed, fail closed; revoked refused)
   → scope gate (in_scope: super-admin all tenants; users only their org)
   → permission gate (issue #12 role pack via server/authz.py)
```

There is no cross-tenant fallback: a tenant-admin token is refused in any
other tenant (`403 scope_denied`) and a role without the route's permission is
refused (`403 permission_denied`).

## Design tokens + provenance

The console styles every frame from the harvested `--os-` design-token twin
(`static/design-tokens/tokens.css` + `tokens.json`). The full provenance table
(upstream repo → file → rev) is in
[`static/design-tokens/PROVENANCE.md`](static/design-tokens/PROVENANCE.md).
Twin parity is asserted in `tests/test_assets.py` so CSS and JSON can never
drift. Dark mode is the `[data-theme='dark']` hook applied per frame (each
same-origin frame links `tokens.css` + `console.css` itself — no cross-frame
cascade reliance).

## Provenance (cannibalization)

Adapted to Python/offline, with nothing copied verbatim, from the fleet's
read-only mirrors (per the cannibalization index, [`docs/CANNIBALIZATION.md`](../docs/CANNIBALIZATION.md)):

| Source | Pattern adapted | Where it landed |
|---|---|---|
| `shared-frontend` `shared/design-tokens/tokens.css` + `tokens.json` + `README` + `docs/DESIGN-TOKENS.md` | normalized design-token system (CSS+JSON twins + provenance, `--os-` namespace) | `static/design-tokens/*` |
| `shared-frontend` `docs/AUTH.md` + `auth/` | RS256 `os-session-token` + JWKS + ROOT_ADMIN allowlist console model | `server/sso.py` (via the merged `identity/sso`) |
| `CMR` `portal/index.html` + `scripts/gen-state.py` | static, no-build single-pane console; "portal is a projection, never a second source of truth" | `static/views/shell.html`, `server/catalog.py` |
| `defragsuite` `services/defrag-portal/main.py` | RBAC policy/intervention control plane + live approve feed | `static/views/policies.html` + `approvals.html`, `server/controls.py` |
| `git-rca-workspace` `portal-backend/app/` | portal-backend auth/session/middleware separation | `server/httpd.py` + `server/app.py` |

## The conversational surface (issue #508, ADR-0023)

`portal/static/views/chat.html` (+ `static/js/chat.js`, served by
`server/chat.py`) is the console's **conversational surface**. It is
gateway-authoritative, exactly as the conversational-surface ADR freezes it:
the portal owns transport, provenance rendering and honest degradation, and
**imports no authority** — `gateway/**`, `identity/**`, `telemetry/**`,
`guardrails/**` and `registry/**` are consumed over HTTP and never imported
from Python.

### The upstream contract it speaks (OpenAI-/Ollama-compatible)

| Call | Purpose |
|---|---|
| `POST {gateway}/v1/chat/completions` with `stream: true` | one turn. The `model` field carries a **tier** token (`LOW/MED/HIGH/MAX`), because the chooser is the authority that resolves a tier to a provider model. `data: {chunk}` frames arrive token by token and end with `data: [DONE]`. |
| the `ao` extension on a chunk | what the surface renders and nothing it invents: `ao.tier.{requested,resolved,resolvedModel}`, `ao.citations.{sources,fragments}`, `ao.degraded.{degraded,reason,fromTier,toTier}`, `ao.grounding.{state,note}`, `ao.usage.{estimatedCostUsd,latencyMs}` |
| `GET {gateway}/v1/ao/finops/budget?tenant=<id>` | the tenant's pre-flight budget state (`action` ∈ `allow/warn/fallback/stop`) |

Point the surface at an authority with `AO_PORTAL_CHAT_GATEWAY` (default
`127.0.0.1:8788`); conversations are stored under `AO_PORTAL_CHAT_STORE`
(default `<repo>/.portal/chat`, never committed).

### Honest degradation is a state machine, not a happy path

| State | Rendered as |
|---|---|
| no envelope / no sources | `grounding.state = NO_DATA` — "no data for this", never an empty success |
| a fragment the envelope does not back | `data-supported="false"` and the label *unsupported — no source in the citations envelope* (shown, never promoted to fact) |
| a fallback-tier answer | `data-degraded="true"` with the reason and the `fromTier → toTier` move |
| usage the read model did not return | `data-usage="no_data"` — never `$0.00` |
| budget `warn`/`fallback` (soft) | `severity: warning`, distinct badge, the turn **still sends** |
| budget `stop` (hard) | `severity: hard_stop`, distinct badge, the turn is **refused** (`402 chat_budget_hard_stop`) before the model call |
| budget unreadable | `severity: no_data`, the turn is refused (`503 chat_budget_no_data`) — the pre-flight check **fails closed**, so a budget that cannot be read is never assumed to be open |
| upstream fault / truncated stream | the turn lands `failed` (`turn.error`) with its partial text kept |

Not-found and refused are different answers on purpose: an unknown tier is
`400 chat_unknown_tier`, a client-supplied provider model is `400
chat_tier_only` (the client selects a *tier*, never a model), and a turn that
was stopped is persisted `cancelled` rather than silently dropped.

The surface ships **feature-flag-gated OFF** in
`infra/feature-flags/registry.yaml` under `surfaces.chat` (read through the
fleet projection's fail-closed `surface_enabled`, so an absent entry is OFF):
while it is off the whole `/api/chat/*` family **and** the view's own static
assets (`views/chat.html`, `js/chat.js`) are absent, and the check runs
**before** AuthN so an unpromoted surface is invisible rather than
distinguishable by an authentication probe. `scripts/check-chat-ux.sh` proves
that control by provoking it.

## Verification (2026-09-08)

- `python3 -m pytest portal/tests -q -p no:cacheprovider` → **{count} passed**
  (SSO/RBAC negatives, control↔policy mapping, audit verify chain, views/API,
  static-asset twins + no-cascade + offline, and the conversational surface
  driven against a loopback fake serving surface). The suite is registered in
  `scripts/pytest-suites.txt` and is exercised by `make verify`.

