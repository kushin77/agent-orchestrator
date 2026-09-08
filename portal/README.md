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

Demo identities (offline seed directory in `server/state.py`):

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
| SSO via console session token (inject #35) + RBAC super-admin vs tenant-admin | `server/sso.py` (real `identity/sso` SsoService, RS256 `os-session-token` + relay + allowlist), `server/authz.py` (issue #12 role pack) |
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
│   ├── sso.py                   # console SSO (injects identity/sso, issue #35)
│   └── state.py                 # seeded offline demo state
├── static/
│   ├── css/console.css          # component/chrome styles (token-driven)
│   ├── design-tokens/           # harvested token system (CSS + JSON twins)
│   ├── js/api.js + console.js   # frame API + shell chrome
│   └── views/*.html             # shell + login + the 9 tenant/console views
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

## Verification (2026-09-08)

- `python3 -m pytest portal/tests -q -p no:cacheprovider` → **90 passed**
  (SSO/RBAC negatives, control↔policy mapping, audit verify chain, views/API,
  static-asset twins + no-cascade + offline).
- `make verify` → green (see PR evidence). The suite is lane-local and is not
  registered in `scripts/pytest-suites.txt` (a foundation/QA-owned file), so
  `make gate`/`make verify` stay green and the suite is exercised here.

