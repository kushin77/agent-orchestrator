# Paperclip-ing integration seam (issue #370)

**Normative.** This is the frozen integration seam between the fleet and the
upstream `paperclipai` operator surface. It is the machine-readable companion to
[ADR-0013](decision-records/ADR-0013-paperclip-ing-integration.md), which decides
the integration mode. The three contracts below are declared again as JSON
Schemas under [`contracts/paperclip/`](contracts/paperclip/heartbeat.schema.json)
and cross-checked by `scripts/check-paperclip-integration.sh` in `make verify`, so
this prose cannot drift from the schemas.

## 1. Mode and boundary

The mode is **adopt the upstream CLI** (`npx paperclipai`) as an external operator
surface, integrated over its HTTP API — **not** embed, **not** fork (ADR-0013).

- Upstream runs as its own process; the fleet keeps running beside it.
- Integration is across a **process boundary**, not a shared runtime.
- The governing rule is ADR-0012's **map the policy, do not couple the runtime**:
  there are **no two authoritative engines**. The fleet remains authoritative for
  dispatch, claims, budgets and audit; upstream is an operator surface mapped over
  the seam.

This doc builds the seam; **it does not stand the process up**. Running
`npx paperclipai onboard --yes` and wiring cross-boundary auth is the adoption
follow-up named in ADR-0013, not work done here.

### Upstream surface (facts, not re-fetched)

- API prefix `/api`; local dev base `http://localhost:3100/api`; company-scoped
  routes `/api/companies/{companyId}/...`.
- `GET /api/health`, `GET /api/openapi.json`.
- Auth — agent: `Authorization: Bearer <agent-key-or-jwt>`; human/board: session
  cookie or `Authorization: Bearer <board-token>`.
- A mutating request made during an agent run carries `X-Paperclip-Run-Id`.
- Resource families: agents, issues, approvals, goals-and-projects, costs,
  activity, dashboard, routines.
- Errors: `400` validation · `401` bad caller identity · `403` known-but-not-allowed
  · `404` missing/outside company scope · `409` conflict/owned/locked · `422`
  business-rule rejection · `503` DB unreachable.

### Fleet-side anchors (verified on disk)

| Anchor | Role |
|---|---|
| `fleet/monitor.py` | writes the monitor rung beat `.fleet/monitor.heartbeat.json` |
| `fleet/brain.py` | writes `.fleet/brain.heartbeat.json`; watches its inbox, never idles |
| `fleet/console.py` | consumes the rung beats for the dashboard |
| `fleet/watchdog.py` | stall/loop/death detection; respawns a missing/stale/drifted rung |
| `fleet/channel.py` + `fleet/schema/message.schema.json` | the directive/ack/result/halt envelope; writes `.fleet/slog.jsonl` |
| `.fleet/slog.jsonl` | the audit stream (gitignored runtime path; `fleet/channel.py` `SLOG`) |
| `telemetry/ledger/` | the append-only, hash-chained ledger |
| `governance/dispatch/` | the claim ledger (`.board/locks/<issue>.lock`, claim/release/reap events) |
| `telemetry/budgets/` + `gateway/finops/` | the budget rail and the FinOps chooser/tiers |
| `fleet/control.py` | operator verbs: `pause` / `resume` / `kill` |

## 2. Contract 1 — heartbeat

**Fleet-side producer:** `fleet/monitor.py` (`write_heartbeat` →
`.fleet/monitor.heartbeat.json`), `fleet/brain.py` (`write_heartbeat` →
`.fleet/brain.heartbeat.json`) and `fleet/terminal.py` (`write_heartbeat` → the
sister rung, `.fleet/sister.heartbeat.json`).
**Fleet-side consumer:** `fleet/console.py` (rung rows) and `fleet/watchdog.py`
(stall/death detection; a beat older than `fleet/channel.py` `STALE_HEARTBEAT_SECONDS`
= 120s is stale).

**Upstream surface it maps to:** the agent heartbeat — event wakes (assigned /
commented / unblocked / review-requested) plus scheduled ticks; a wake carries the
delta so the agent does not re-read history; watchdogs detect stall/loop/death and
restart or escalate naming the exact stop point; every heartbeat ends in durable
progress or a named blocker with an owner.

**Fleet heartbeat shape, as shipped** (`fleet/monitor.py` / `fleet/brain.py`
`write_heartbeat`), flat:

```json
{"pid": 1234, "state": "healthy", "started_at": "…Z", "commit": "<sha>", "ts": "…Z"}
```

**Seam field mapping** (schema: [`heartbeat.schema.json`](contracts/paperclip/heartbeat.schema.json)):

| Seam field | Fleet source | Upstream surface |
|---|---|---|
| `agent_id` | the rung / agent id (monitor, brain, sister) | agent record id |
| `session_id` | `AO_SESSION_ID` (minted by `governance/isolation/identity.py`) | agent run identity |
| `tick` | derived from the emit count | scheduled tick counter |
| `cadence_seconds` | `fleet/monitor.py` `POLL_SECONDS` (=20) | tick cadence |
| `wake.cause` | the event that fired the beat | assigned / commented / unblocked / review-requested / scheduled |
| `wake.delta` | the change since the last beat | the delta the wake carries |
| `outcome.status` | `progress` \| `blocked` | durable progress vs named blocker |
| `outcome.owner` | the accountable owner named for the blocker | blocker owner |
| `ts` | the beat `ts` | beat timestamp |

## 3. Contract 2 — ticket

**Fleet-side producer:** `governance/dispatch/` (claim / release / reap events,
`.board/locks/<issue>.lock`).
**Fleet-side consumer:** `fleet/console.py` and `fleet/report.py` (the dispatch
ledger view and status/report lines).
**Fleet-side audit:** `.fleet/slog.jsonl` (`fleet/channel.py`) and `telemetry/ledger/`.

**Upstream surface it maps to:** the issue — one accountable owner; statuses
in-progress / blocked / in-review / done; explicit blocked-by relations; plan →
child-issue fan-out with parallel tracks; full tool-call tracing; immutable
append-only audit log; "done is a verdict, not a self-report".

**Seam field mapping** (schema: [`ticket.schema.json`](contracts/paperclip/ticket.schema.json)):

| Seam field | Fleet source | Upstream surface |
|---|---|---|
| `id` | the issue number (`kushin77/agent-orchestrator#N`) | issue id |
| `owner` | the claiming agent (single) | accountable owner |
| `status` | derived from claim state + lifecycle | in-progress / blocked / in-review / done |
| `blocked_by` | the issue's chain edges (`.fleet/sent` directives, dependency order) | blocked-by relations |
| `goal` | the epic the issue serves (e.g. EPIC-00 = issue #4) | goal / project reference |
| `evidence` | the `Verify:` command + its real output (GR-12) | evidence on the issue |

### 3.1 Ticket contract v2 — the single join node (ADR-0014, #400)

[ADR-0014](decision-records/ADR-0014-ticket-single-join-node-contract-v2.md)
decides that **the ticket is the single join node** (mesh → hub): tasks, agents,
the lessons register and the PMO all join on the ticket. v2 is **additive** — the
six v1 keys above keep their meaning and a v1 document still validates
([`ticket.example.v1.json`](contracts/paperclip/ticket.example.v1.json)); the
canonical v2 document is
[`ticket.example.json`](contracts/paperclip/ticket.example.json). v2 adds `kind`,
`facets` and `authority`:

```json
{
  "id": "kushin77/agent-orchestrator#359",
  "kind": "task",
  "owner": "<AO_SESSION_ID>",
  "status": "done",
  "blocked_by": ["#370"],
  "goal": "#338",
  "evidence": [
    {"kind": "gate-run", "ref": "<attestation git_sha>", "result": "PASS", "checks": 29}
  ],
  "facets": {
    "lessons": {"incident": "INC-0001", "rca": "RCA-0001",
                "corrective_actions": ["CA-0001"], "class": "faang"},
    "raid": {"risk": "high", "remediation": "#170"},
    "budget": {"scope": {"level": "agent", "id": "paperclip"},
               "spent": 1.23, "cap": 5.00, "receipt": "<ledger ref>"}
  },
  "authority": {
    "owner": "governance/isolation",
    "status": "governance/dispatch",
    "goal": ".board/snapshot.json",
    "blocked_by": ".board/snapshot.json",
    "facets.lessons": "governance/lessons",
    "facets.raid": "derived",
    "facets.budget": "telemetry/budgets"
  }
}
```

**The four rules v2 freezes** (enforced by
`scripts/check-paperclip-integration.sh`, so a bad contract fails by name):

1. **`kind`** ∈ `task | incident | rca | corrective-action | lesson | suggestion`.
   The **generic improvement register is a ticket kind** — this is how an open
   `SUGGEST-*` becomes an addressable node instead of a ledger id with no shape.
2. **`facets` is a closed set** (`lessons`, `raid`, `budget`). An unknown facet
   fails; a new facet is a contract change, not a silent extension.
3. **`authority` is the one-writer-per-field map.** Each populated field names
   **exactly one** producing lane; a field with two writers fails and a populated
   field with no declared writer fails. The ticket is a **projection, never
   authority** — a projection is only rebuildable when every field has exactly one
   source, and that source is a real authoritative surface.
4. **`evidence[]` entries are structured**, additively: a receipt object
   `{kind, ref, result, checks}` alongside the v1 string form, so the same receipt
   that proves delivery also backs the budget charge (mismatches #6 and #10).

**Authority table** (the frozen one-writer map — the join's single source of
truth per field):

| Ticket field | Single writer (`authority`) | Surface it projects |
|---|---|---|
| `owner` | `governance/isolation` | the minted session identity (ADR-0011, #263) |
| `status` | `governance/dispatch` | the claim ledger / lifecycle |
| `goal` | `.board/snapshot.json` | the epic the ticket serves |
| `blocked_by` | `.board/snapshot.json` | the dependency chain edges |
| `facets.lessons` | `governance/lessons` | the lessons register |
| `facets.raid` | `derived` | computed from the ticket's risk signals |
| `facets.budget` | `telemetry/budgets` | the budget rail |

`id` and `kind` are the node's own identity (not authority-tracked); `evidence`
is appended by whichever lane ran the proof (not authority-tracked).

## 4. Contract 3 — budget

**Fleet-side producer:** `governance/finops/` (tier vocabulary, chooser),
`gateway/finops/` (`budget.py`: `warn_at_pct`, `hard_cap_pct`), `telemetry/budgets/`
(`budget.py`, `VendorBudgetCap`: `window` = `month`, `limitUsd`, `warnAtPct`).
**Fleet-side consumer:** `telemetry/budgets/chargeback.py` (per-tenant chargeback
roll-ups over the metering store) and `telemetry/budgets/exporter.py` (the
budget/quota/kill-switch state snapshot), plus `telemetry/budgets/` (`quota.py`,
`killswitch.py`, `audit.py`).

**Upstream surface it maps to:** costs — cap per agent (also per team/project);
hard stop at the cap with a graceful hand-off; top-up is an approval; burn-rate
alerts before the stop; roll-ups by agent/goal/time; per-task receipts.

**Seam field mapping** (schema: [`budget.schema.json`](contracts/paperclip/budget.schema.json)):

| Seam field | Fleet source | Upstream surface |
|---|---|---|
| `scope.level` | `agent` \| `team` \| `project` | cap scope |
| `scope.id` | the agent / team / project id | scoped subject |
| `period` | `month` (`telemetry/budgets/budget.py` cap window) | budget period |
| `cap` | `limitUsd` / the tenant cost+tokens rail | the cap |
| `spent` | aggregated month spend | spend to date |
| `currency` | `USD` (vendor caps) | currency |
| `hard_stop` | `gateway/finops/budget.py` `hard_cap_pct` (default 100) | hard stop with graceful hand-off |
| `burn_rate_alert_pct` | `warn_at_pct` / `hard_cap_pct` | burn-rate alert before the stop |
| `receipt_ref` | the audit/ledger entry (`telemetry/budgets/audit.py`, `telemetry/ledger/`) | per-task receipt |

## 5. Mismatches (the real deliverable)

The fleet's shape does **not** match upstream's exactly. The happy-path table above
is the mapping; these are the places where an adapter is required. Each is a
work-order item for the adoption, not a claim that the seam is free.

**Status after ticket contract v2 (ADR-0014, #400).** v2 freezes the ticket as the
single join node and makes its evidence structured, which closes the four
ticket/evidence/budget-receipt mismatches below and leaves the heartbeat and
auth mismatches as adoption work:

| # | Mismatch | Status after v2 |
|---|---|---|
| 1 | Heartbeat granularity | **OPEN** — heartbeat schema untouched by v2 |
| 2 | Heartbeat state vocabulary | **OPEN** — heartbeat schema untouched by v2 |
| 3 | Cadence is implicit | **OPEN** — heartbeat schema untouched by v2 |
| 4 | Ticket status is not first-class | **CLOSED** — `status` is a first-class ticket field with a single declared producer (`authority.status = governance/dispatch`) |
| 5 | No goal reference on a claim | **CLOSED** — `goal` is a ticket field; `authority.goal = .board/snapshot.json` |
| 6 | Evidence is a convention, not a field | **CLOSED** — `evidence[]` is a field and admits a structured receipt |
| 7 | Budget scope differs | **SHAPE CLOSED / PRODUCER OPEN** — `facets.budget.scope{level,id}` admits `agent`/`team`/`project`; the fleet producer for `scope.level = agent` is still adoption work |
| 8 | No explicit currency | **OPEN** — the ticket facet does not add a currency column; the budget rail still encodes USD by convention |
| 9 | Hard stop is a percentage, not a boolean | **OPEN** — upstream's absolute cap/stop is not modelled by the ticket facet |
| 10 | No per-task receipt field | **CLOSED** — `facets.budget.receipt` and the structured `evidence[]` receipt carry the same receipt id, tying spend to the task |
| 11 | Auth models do not meet natively | **OPEN** — cross-boundary auth is one-time adoption work, not a field mapping |

1. **Heartbeat granularity.** The fleet beat is a flat process-liveness record
   (`pid`, `state`, `started_at`, `commit`, `ts`) — it has **no** `wake.cause`, no
   `wake.delta` and no `outcome`. Upstream a wake carries a delta and ends in
   progress-or-blocker. The seam *adds* fields the fleet does not emit today; an
   adapter must synthesize `wake` and `outcome` from the rung's actual activity.
   **Resolved** by `integrations/paperclip/mapping.py` (`map_heartbeats`
   synthesizes `wake` from the rung state and `outcome` from liveness).
2. **Heartbeat state vocabulary.** The fleet `state` is a rung-liveness enum
   (`healthy`, `idle`, `dispatching`, plus console-derived `down` / `no-heartbeat`);
   upstream has no such enum — it distinguishes event wake from scheduled tick. The
   two are not translations of each other. **Resolved** by
   `integrations/paperclip/mapping.py` (`_WAKE_CAUSE` maps the rung state onto the
   upstream `wake.cause` enum; a rung with no beat becomes a named blocker).
3. **Cadence is implicit.** The fleet has no tick counter and no explicit cadence
   field; `POLL_SECONDS` (=20) and `STALE_HEARTBEAT_SECONDS` (=120) are module
   constants. Upstream has an explicit scheduled tick. `tick` and `cadence_seconds`
   must be derived, not read. **Resolved** by `integrations/paperclip/mapping.py`
   (`cadence_seconds` = `fleet/monitor.py` `POLL_SECONDS` = 20; `tick` derived and
   held at 0 offline).
4. **Ticket status is not first-class.** `governance/dispatch/` records claim
   *events* (`claim` / `release` / `reap`) and lock files — there is no
   `in-progress` / `blocked` / `in-review` / `done` enum and **no `blocked_by`
   field**. Status and blocked-by must be derived from claim state plus the
   dependency chain; there is no single record to read them from. **Resolved** by
   `integrations/paperclip/mapping.py` (`_ticket_status` derives the closed-vocabulary
   status; `blocked_by` is joined from the board snapshot).
5. **No goal reference on a claim.** A claim names the issue, agent and lane — not
   the epic it serves. `goal` must be joined from the board, not read from the
   ledger. **Resolved** by `integrations/paperclip/mapping.py` (`map_tickets` joins
   `goal` from the snapshot's milestone/parent).
6. **Evidence is a convention, not a field.** "Evidence" today is the PR + the
   gate output posted to the issue (GR-12), not a structured field on a record.
   The `evidence` array is a seam field with no fleet column behind it. **Resolved
   (derived)** by `integrations/paperclip/mapping.py` (`evidence` carries the issue
   reference and, when present, the closure or commit reference). **Deferred** — a
   natively structured evidence field is adoption work owned by the ticket-contract
   v2 lane (#401, milestone M27).
7. **Budget scope differs.** The fleet budget rail is scoped by **tenant and
   vendor** (`telemetry/budgets/budget.py` `VendorBudgetCap`: per-vendor/model,
   `window` = `month`), not by **agent / team / project**. Upstream caps per agent.
   `scope.level` = `agent` has **no fleet producer today**. **Deferred** —
   `integrations/paperclip/mapping.py` (`map_budgets`) maps a tenant budget to
   scope level `team`; a real per-agent cap has no fleet producer and is adoption
   work owned by the budget-scope adapter lane (#415, milestone M27).
8. **No explicit currency.** The fleet encodes USD by convention (`limitUsd`); it
   has no `currency` field. Upstream models currency. `currency` is derived.
   **Resolved (derived)** by `integrations/paperclip/mapping.py` (`currency` =
   `USD`).
9. **Hard stop is a percentage, not a boolean.** The fleet expresses the stop as
   `hard_cap_pct` (default 100); upstream a hard stop is an absolute cap with a
   graceful hand-off. `hard_stop` (boolean) and `cap`/`spent` must be derived from
   the percentage rail. **Resolved (derived)** by
   `integrations/paperclip/mapping.py` (`hard_stop` from the mode/percentage rail).
10. **No per-task receipt field.** The fleet has an append-only audit stream
    (`.fleet/slog.jsonl`) and a hash-chained ledger (`telemetry/ledger/`) but no
    field that ties a spend to a task as a receipt. `receipt_ref` is a seam field
    pointing at an audit/ledger entry, not a native receipt. **Resolved (derived)**
    by `integrations/paperclip/mapping.py` (`receipt_ref` points at the budget
    config row). **Deferred** — a native per-task receipt is adoption work owned by
    the budget-scope adapter lane (#415, milestone M27).
11. **Auth models do not meet natively.** The fleet has no HTTP caller identity;
    upstream has agent keys/JWTs, a session cookie, a board token and the
    `X-Paperclip-Run-Id` run header. Cross-boundary auth is one-time integration
    work, not a field mapping. **Resolved** by
    `integrations/paperclip/client.py` (`Authorization: Bearer` on every request
    and `X-Paperclip-Run-Id` on mutating calls, applied at the transport seam).
    **Deferred** — standing the process up and wiring real cross-boundary
    credentials is adoption work owned by the cross-boundary auth lane (#412,
    milestone M27).

Every mismatch above is now either **resolved** by a file under
`integrations/paperclip/` (enforced offline by
`scripts/check-paperclip-integration-adapter.sh` in `make verify`) or **deferred**
to the named adoption lane with the reason recorded on the row.

## 6. Related records and docs

- [ADR-0013](decision-records/ADR-0013-paperclip-ing-integration.md) — the mode
  decision this seam implements (adopt the CLI).
- [ADR-0012](decision-records/ADR-0012-hermes-paperclip-boundary.md) — the
  ownership boundary and the *map the policy, do not couple the runtime* rule.
- [ADR-0011](decision-records/ADR-0011-session-fleet-transport.md) — the transport
  decision the fleet's message envelope follows.
- `docs/PAPERCLIP-ING-GAP-ANALYSIS.md` — the gap analysis this seam builds on
  (sibling issue #368; referenced in plain text, not linked, so this doc does not
  depend on that lane's landing order).
- `docs/FLEET-DASHBOARD-GAP-ANALYSIS.md` — the dashboard gap analysis (another
  lane's doc; mentioned in plain text only).
- [`../GOLDEN-RULES.md`](GOLDEN-RULES.md) — the golden rules cited above (GR-10,
  GR-12) and [AGENTS.md](../AGENTS.md) for the doctrine.
