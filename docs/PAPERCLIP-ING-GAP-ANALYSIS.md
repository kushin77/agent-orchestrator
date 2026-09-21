# paperclip.ing gap analysis — upstream capability families vs this fleet

Issue: [#368](https://github.com/kushin77/agent-orchestrator/issues/368) ·
Retrieved: **2026-09-13**

## Why this doc exists

`paperclip.ing` is a commercial, open-source multi-agent product whose model is
close enough to this control plane's own shape that an operator will reasonably
ask "do we already have this?". This is a **fork-map / adoption decision**, not a
vendoring: it names, family by family, which of upstream's capability families
this fleet already ships — **and cites the fleet's own file path for each** — so
the decision to adopt, adapt, or ignore is sourced rather than assumed. It is the
same discipline as the sibling terminal-vs-web inventory in
[`FLEET-DASHBOARD-GAP-ANALYSIS.md`](FLEET-DASHBOARD-GAP-ANALYSIS.md).

## Upstream facts (as retrieved)

| Fact | Value |
|---|---|
| Product | **paperclip.ing** — "A team of agents for every person." |
| Source repository | `paperclipai/paperclip` |
| License | **MIT** |
| Self-host | `npx paperclipai onboard --yes` |
| Docs | `docs.paperclip.ing` |
| Latest release seen | **v2026.831.1** (2026-09-02) |
| Popularity | ~81k GitHub stars |
| API prefix | `/api` (local dev `http://localhost:3100/api`) |
| Control-plane routes | company-scoped: `/api/companies/{companyId}/...` |
| Auth | board session cookie / `Authorization: Bearer <board-token>` (humans); `Authorization: Bearer <agent-key-or-jwt>` (agents) |
| Agent-run correlation | mutating requests during a run carry `X-Paperclip-Run-Id` |
| Health / machine surface | `GET /api/health`, `GET /api/openapi.json` |
| Resource families | companies, agents, issues, approvals, goals-and-projects, costs, secrets, activity, dashboard, routines |
| Error codes | `400/401/403/404/409/422/503/500`; bodies validated server-side with Zod |
| Extensibility | skills (`SKILL.md`), extensions/plugins, MCP tool access |

## Namesake disambiguation — four distinct "paperclips"

"paperclip" means four unrelated things in this fleet. Conflating them is the
single most likely source of a wrong adoption call, so they are named here:

1. **The fleet agent** `paperclip` — a research / docs-authoring persona in the
   five-agent team: `registry/profiles/seeds/paperclip.1.0.0.yaml` and
   `registry/personas/cards/paperclip.yaml`; bundled by the signed team pack
   `registry/packs/releases/purebliss-team.1.0.0.yaml`.
2. **The gateway provider** `paperclip` — an OpenAI-compatible provider adapter,
   `gateway/providers/paperclip.py`, routing the `paperclip-planner` model.
3. **The vendored CMR planning module** `paperclip` — a hub module pinned inside
   the `vendor/CMR` submodule at `vendor/CMR/catalog/modules/paperclip`
   (the adapter above records that origin; see issue #124).
4. **The upstream product** `paperclip.ing` — the external SaaS product this
   document analyses. It is **not** any of the three fleet artifacts above.

The boundary between (1) and (2) is recorded in
[`decision-records/ADR-0012-hermes-paperclip-boundary.md`](decision-records/ADR-0012-hermes-paperclip-boundary.md).
The authoritative-artifact resolution for all four names — and the paired
three-way "Hermes" disambiguation — is
[`decision-records/ADR-0033-paperclip-hermes-naming-resolution.md`](decision-records/ADR-0033-paperclip-hermes-naming-resolution.md).

## Capability family map

Upstream's six capability families are compared feature by feature against the
fleet primitive that already covers each. Verdicts: **ALREADY-SHIPPED** (the
fleet has the capability, sourced below), **ADAPT** (the fleet has the mechanism,
but not upstream's exact surface), **GAP** (no fleet primitive today).

### 1. Org chart

Upstream models hierarchies, roles and reporting lines — "if it can receive a
heartbeat, it's hired" — and requires approval before an agent can hire another.

| Upstream feature | Fleet primitive (file path) | Verdict |
|---|---|---|
| Agent records with role / tier | `registry/profiles/seeds/paperclip.1.0.0.yaml`, `registry/profiles/catalog.yaml` | ALREADY-SHIPPED |
| Persona = role + lens | `registry/personas/cards/` (one card per persona) | ALREADY-SHIPPED |
| Bundled, signed org unit | `registry/packs/releases/purebliss-team.1.0.0.yaml` | ALREADY-SHIPPED |
| Hire requires approval | lane ownership + claim gate: `governance/dispatch/cli.py`, `governance/dispatch/order.py` | ADAPT |

### 2. Goal alignment

Upstream chains mission → project goal → agent goal → task, requires every task
to cite the goal it serves, and flags orphaned work.

| Upstream feature | Fleet primitive (file path) | Verdict |
|---|---|---|
| Task cites the goal it serves | every issue carries its epic/chain in `docs/EXECUTION-PLAN.md`, claimed via `governance/dispatch/cli.py` | ADAPT |
| Orphaned work is flagged | `governance/dupcheck/`, `governance/board/` | ADAPT |
| Wave / dependency-ordered dispatch | `fleet/brain.py` (wave plan), `docs/EXECUTION-PLAN.md` | ALREADY-SHIPPED |
| Goal roll-up | `fleet/report.py` + `fleet/console.py` WAVES rung | ADAPT |

### 3. Heartbeats

Upstream wakes agents on events (assigned, commented, unblocked, review
requested) and on schedules; the wake payload carries the delta so history is not
re-read; **watchdogs** detect stall / loop / death on long runs and restart or
escalate naming the exact stop point; every heartbeat ends in durable progress.

| Upstream feature | Fleet primitive (file path) | Verdict |
|---|---|---|
| Per-rung heartbeat file | `fleet/channel.py`, `.fleet/brain.heartbeat.json` / `.fleet/monitor.heartbeat.json` (runtime, gitignored) | ALREADY-SHIPPED |
| Per-session heartbeat + TTL | `governance/reconcile/heartbeat.py` (`.fleet/sessions/`) | ALREADY-SHIPPED |
| Event wake with delta payload | `fleet/channel.py` + `fleet/schema/message.schema.json` (`correlation_id`, `nonce`) | ALREADY-SHIPPED |
| Watchdog detects stall / loop / death | `fleet/watchdog.py` (respawn verification), `fleet/monitor.py` | ALREADY-SHIPPED |
| Restart-or-escalate naming the stop point | `fleet/watchdog.py` (`RESPAWN FAILED`), `governance/reconcile/sweep.py` (reclaim / park / shelve) | ALREADY-SHIPPED |
| Heartbeat ends in durable progress | `fleet/monitor.py` + `fleet/console.py` rung health | ADAPT |

### 4. Budgets & costs

Upstream gives each agent a monthly budget (also per team/project) with a
**hard cap**; at the cap the agent stops and asks for more; top-ups are an
approval, not a config edit; burn-rate alerts fire before the stop; costs roll up
by agent/model/goal/week; each task gets a receipt; per-role model assignment
sends cheap work to cheap models.

| Upstream feature | Fleet primitive (file path) | Verdict |
|---|---|---|
| Per-agent / per-tenant budget with hard cap | `gateway/finops/budget.py`, `gateway/finops/budgets.yaml` (`hardCapPct` absolute stop) | ALREADY-SHIPPED |
| Kill-switch and quotas | `telemetry/budgets/config/killswitch.yaml`, `quotas.yaml`, `policies.yaml` | ALREADY-SHIPPED |
| Cost roll-up by agent / model / week | `telemetry/metering/intake.py`, `telemetry/metering/rate_cards/`, `telemetry/ledger/` | ALREADY-SHIPPED |
| Per-task receipts | `telemetry/ledger/store.py` (`audit_event.schema.json`) | ADAPT |
| Per-role model assignment (cheap work → cheap model) | `gateway/finops/chooser.py` + `gateway/finops/tiers.yaml` (L0/L1/L2 ladder) | ALREADY-SHIPPED |
| Top-up is an approval, not a config edit | `fleet/directive.json` + brain directive ledger (`.fleet/sent/`) | ADAPT |

### 5. Tickets + audit

Upstream uses structured tickets with one accountable owner, statuses
in-progress / blocked / in-review / done, explicit blocked-by trees, plan →
child-issue fan-out with parallel tracks, full tool-call tracing, an **immutable
append-only audit log**, and "done is a verdict, not a self-report".

| Upstream feature | Fleet primitive (file path) | Verdict |
|---|---|---|
| One accountable owner per item | `governance/dispatch/claims.py` (one claim per issue) | ALREADY-SHIPPED |
| Status / blocked-by tree | `governance/dispatch/order.py`, `governance/dispatch/snapshot.py` | ALREADY-SHIPPED |
| Plan → child-issue fan-out, parallel tracks | `.fleet/waves/` wave plan driven by `fleet/brain.py`; lane contract in `docs/EXECUTION-PLAN.md` | ALREADY-SHIPPED |
| Full tool-call tracing | `fleet/channel.py`, `.fleet/slog.jsonl` (append-only rung log) | ADAPT |
| Immutable append-only audit log | `telemetry/ledger/` (`store.py`, `verify.py`, `crypto.py`) | ALREADY-SHIPPED |
| "Done is a verdict" (QA handoff) | `governance/lifecycle/closeout.py`, `governance/lifecycle/audit.py` | ALREADY-SHIPPED |

### 6. Governance

Upstream lets an operator approve hires, approve/override strategy, and pause /
resume / override / reassign / terminate any agent at any time — autonomy is
granted, not default.

| Upstream feature | Fleet primitive (file path) | Verdict |
|---|---|---|
| Pause / resume the fleet | `fleet/control.py` (tmux/rung control), `fleet/singleton.py` (single-writer guard) | ALREADY-SHIPPED |
| Override / reassign a lane | `governance/dispatch/cli.py` (release + re-claim), `fleet/directive.json` | ADAPT |
| Terminate / reap an agent | `fleet/prune.py`, `governance/reconcile/sweep.py` | ALREADY-SHIPPED |
| Autonomy granted, never default | `governance/lifecycle/closeout.py` (close with evidence), `guardrails/policy/` | ALREADY-SHIPPED |
| Human approval gate for a hire | claim gate: `governance/dispatch/order.py` | ADAPT |
| Skills / plugins / MCP tools | `guardrails/` + `gateway/mcp/` | ADAPT |

## Cannibalize vs build

The fleet already owns the hard parts of every family above. An integration is
therefore an **assembly** job — cannibalize the existing primitive, build only
the thin surface upstream needs that the fleet genuinely lacks.

| Upstream surface | Cannibalize (use as-is or port) | Build (new) |
|---|---|---|
| Company-scoped `/api/companies/{companyId}/...` | route idiom + tenant scoping from `identity/cpapi/`, `control-plane/sdk/` | the paperclip-company mapping layer |
| Board session cookie / `Bearer <board-token>` (humans) | `identity/rbac/`, `identity/sso/` | the board-token adapter |
| `Bearer <agent-key-or-jwt>` (agents) | agent identity records in `registry/profiles/` | the agent-key/JWT mint-and-verify binding |
| `X-Paperclip-Run-Id` correlation header | `fleet/channel.py` `correlation_id`, `fleet/schema/message.schema.json` | the header↔correlation_id bridge |
| `GET /api/health` | `fleet/health.py` rung health | the HTTP projection |
| `GET /api/openapi.json` | `control-plane/sdk/` client contract | the OpenAPI emitter |
| Resource families (issues, approvals, costs, secrets, activity) | `governance/dispatch/`, `telemetry/ledger/`, `telemetry/metering/` | the REST resource bindings |
| Zod request validation + error taxonomy | `guardrails/policy/` schema validation | the `400/409/422/503` mapping |
| Skills (`SKILL.md`) + plugins + MCP | `guardrails/` + `gateway/mcp/` | the paperclip-skill loader |

## GR-10 provenance

Upstream is analysed from its public documentation and repository surface only.

- **Source:** `paperclipai/paperclip` (upstream product **paperclip.ing**);
  docs at `docs.paperclip.ing`.
- **License:** MIT.
- **Version:** v2026.831.1 (2026-09-02).
- **Retrieved:** 2026-09-13.
- **Use:** reference / pattern only. **No upstream code is copied into this
  repository.** Nothing under `paperclipai/paperclip` is vendored, and no
  upstream file is a dependency of any fleet primitive named above.

## What this round does NOT do

- **No vendoring.** It does not vendor upstream code — this is a fork-map, not
  an import.
- It does **not** remove `fleet/console.py`; the terminal TUI stays the local
  operator's instrument (same posture as
  [`FLEET-DASHBOARD-GAP-ANALYSIS.md`](FLEET-DASHBOARD-GAP-ANALYSIS.md)).
- It does **not** touch the fleet agent, the gateway provider, or the vendored
  CMR planning module named in the disambiguation above.
- The integration surface itself is a separate lane (issue #370), which lands
  after this document.

## Cross-references

- [`FLEET-DASHBOARD-GAP-ANALYSIS.md`](FLEET-DASHBOARD-GAP-ANALYSIS.md) — the
  sibling gap analysis (terminal TUI vs web single-pane-of-glass).
- [`EXECUTION-PLAN.md`](EXECUTION-PLAN.md) — the one-issue-one-lane dispatch
  contract referenced by families 2 and 5.
- [`decision-records/ADR-0012-hermes-paperclip-boundary.md`](decision-records/ADR-0012-hermes-paperclip-boundary.md)
  — the fleet-agent / gateway-provider boundary.
