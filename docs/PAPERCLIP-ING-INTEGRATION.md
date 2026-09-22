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

**Status (2026-09-21).** This section states the *mode*; it is not a claim that
upstream is running. `enable_paperclip` defaults `true` (AO-GR-6), the flag
gates the count-gated Cloud Run v2 runtime declared under
`infra/paperclip/terraform/`, and the seam is scoped to the three contracts
below — paperclip does not execute code and does not dispatch tasks. The
plain-language current state is
in [`PAPERCLIP-ING-GAP-ANALYSIS.md`](PAPERCLIP-ING-GAP-ANALYSIS.md), section
*Current status*.

- Upstream runs as its own process; the fleet keeps running beside it.
- Integration is across a **process boundary**, not a shared runtime.
- The governing rule is ADR-0012's **map the policy, do not couple the runtime**:
  there are **no two authoritative engines**. The fleet remains authoritative for
  dispatch, claims, budgets and audit; upstream is an operator surface mapped over
  the seam.
- The declared policy registry (`governance/policy/registry.py`, #1763) is
  delivered to upstream **read-only** by `integrations/paperclip/policy_map.py`,
  which projects the registry's own rows and re-derives no policy domain itself
  (issue #1764).

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
| 11 | Auth models do not meet natively | **RESOLVED (seam)** — the cross-boundary auth seam landed (#412, §5.2): agent key/JWT mint+verify from the registry records, the board-token adapter over the fleet session path, and the `X-Paperclip-Run-Id` ↔ `correlation_id` run bridge |

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
    work, not a field mapping. **Resolved (transport)** by
    `integrations/paperclip/client.py` (`Authorization: Bearer` on every request
    and `X-Paperclip-Run-Id` on mutating calls, applied at the transport seam).
    **Resolved (seam)** by `integrations/paperclip/auth/` — the cross-boundary
    auth seam mints and verifies an agent key/JWT from the fleet's own registry
    records (no second identity store), maps a human operator onto the board
    session path (no third login), and bridges the upstream run id to the
    fleet's `correlation_id` (#412, §5.2). Standing the process up with real
    credentials is deployment work, not a missing mapping.

### 5.1 Resolution record — #428 (commit `9cd5794`)

The lane that resolves this mismatch list is **#428** (PR #433, commit
`9cd5794`) — the `integrations/paperclip/**` boundary adapter, enforced offline
by `scripts/check-paperclip-integration-adapter.sh` in `make verify`. Each row
below records the honest verdict: **resolved** where a file under
`integrations/paperclip/` answers the mismatch, **deferred (derived)** where the
mapping is derived but the native fleet producer is still adoption work owned by
a named lane. A deferred row is not a resolved one.

| # | Mismatch | Verdict by #428 | Where / owner |
|---|---|---|---|
| 1 | Heartbeat granularity | **resolved** | `integrations/paperclip/mapping.py` (`map_heartbeats`) |
| 2 | Heartbeat state vocabulary | **resolved** | `integrations/paperclip/mapping.py` (`_WAKE_CAUSE`) |
| 3 | Cadence is implicit | **resolved** | `integrations/paperclip/mapping.py` (derived `cadence_seconds`/`tick`) |
| 4 | Ticket status is not first-class | **resolved** | `integrations/paperclip/mapping.py` (`_ticket_status`) |
| 5 | No goal reference on a claim | **resolved** | `integrations/paperclip/mapping.py` (`map_tickets`) |
| 6 | Evidence is a convention, not a field | **resolved (derived); field deferred** | mapped in `mapping.py`; native structured field owned by #401 |
| 7 | Budget scope differs | **deferred (producer)** | shapes mapped in `mapping.py`; per-agent producer owned by #415 |
| 8 | No explicit currency | **resolved (derived)** | `integrations/paperclip/mapping.py` (`currency`) |
| 9 | Hard stop is a percentage, not a boolean | **resolved (derived)** | `integrations/paperclip/mapping.py` (`hard_stop`) |
| 10 | No per-task receipt field | **resolved (derived); receipt deferred** | `receipt_ref` in `mapping.py`; native receipt owned by #415 |
| 11 | Auth models do not meet natively | **resolved (transport); adoption deferred** | `integrations/paperclip/client.py`; cross-boundary auth owned by #412 |

The canonical home of the boundary adapter is `integrations/paperclip/` — see
[`../integrations/paperclip/README.md`](../integrations/paperclip/README.md). A
second top-level paperclip module is refused by name by
`scripts/check-paperclip-canonical-module.sh` (issue #448).

Every mismatch above is now either **resolved** by a file under
`integrations/paperclip/` (enforced offline by
`scripts/check-paperclip-integration-adapter.sh` in `make verify`) or **deferred**
to the named adoption lane with the reason recorded on the row.

### 5.2 Resolution record — #412 (the cross-boundary auth seam)

Mismatch 11 has two halves, and they land in two places. The **transport** half
(attach `Authorization: Bearer` and `X-Paperclip-Run-Id`) is answered by
`integrations/paperclip/client.py` (#428). The **identity** half — the two auth
models actually meeting — is answered by **#412** in
[`../integrations/paperclip/auth/`](../integrations/paperclip/auth/README.md),
enforced offline by `scripts/check-paperclip-auth.sh` in `make verify`:

| Half of mismatch 11 | Where it is answered |
|---|---|
| Agent identity minted/verified from the fleet's own records (company scope in the claim, no parallel identity store) | `auth/registry.py` reads `registry/profiles/seeds/*.yaml` through `integrations/paperclip/mapping.py`; an unregistered agent cannot be minted for and a retired one cannot verify (ADR-0012) |
| Human identity mapped onto the board session path (no third login) | `auth/board.py` mints the board token from a `governance/isolation` `SessionIdentity` record and re-checks the session on verify (`session_revoked`) |
| Run correlation bridged (`X-Paperclip-Run-Id` ↔ `correlation_id`) | `auth/runbridge.py` joins the two, single-use; the fleet value is the authoritative one (`fleet/schema/message.schema.json` requires `correlation_id`) |
| Negative controls first-class and proven | `auth/guard.py` + `auth/cli.py controls`; each refusal (401 expired / 401 unknown / 401 missing Authorization / 403 cross-company / 403 permission_denied never 404 / 409 replayed run id) is provoked and refused by name |

The standing-up of the process with real credentials is **deployment** work, not
a missing mapping; the seam itself is closed here.

### 5.3 The control-verb mismatches (RC-6, #557)

The mismatches above are in the seam's **contract fields** (heartbeat, ticket,
budget, auth). A second class is the **control vocabulary**: this fleet declares
its own operator verbs, upstream serves control routes on **its** server, and the
two are the same *kind* of thing with only the reach differing (ADR-0025 §3.3) —
which is exactly the shape that makes a reader assume an equivalence that does not
hold.

**RC-6 (#557) measured the whole correspondence** in
[`../integrations/paperclip/control_mapping.py`](../integrations/paperclip/control_mapping.py)
(commit `7d0534d`, PR #576; exercised offline by the `integrations/paperclip`
suite declared in `scripts/pytest-suites.txt`). It walks the **49** verbs declared
in `control-plane/control/verbs.yaml` (RC-2, #553) against the upstream control
routes inventoried from upstream's own route files, and records a row per verb:
**8 `MAPPED`, 8 `MISMATCH`, 33 `UNMAPPED`**.

The table below is the nine rows RC-6 **recorded as mismatches** — eight are
`MISMATCH` rows in the verb table; the ninth (`M-CTL-1`) is upstream-only, which
is why it names no fleet verb. The `#` column carries RC-6's own immutable row ids
(the ids its suite asserts by name), so a row here and the row in the module cannot
drift apart; the divergence column states the **kind** RC-6 recorded and the reason
it recorded. Nothing here adds a claim RC-6 did not measure — the module holds the
full statement per row.

| # | Mismatch | Fleet verb | Upstream route | Divergence (RC-6) |
|---|---|---|---|---|
| `M-CTL-1` | upstream `terminate` has **no fleet counterpart** | — | `POST /agents/:id/terminate` | `no-fleet-counterpart` — upstream terminate ends an agent irreversibly (it cancels that agent's runs and wakeups and invalidates its descendants); **no** verb in our vocabulary ends an agent. `fleet.stop`/`kill`/`halt` take down the local loop, and our only irreversible verb (`fleet.override`) files a ticket |
| `M-CTL-2` | our fleet-wide `pause` has no **per-agent** upstream counterpart | `fleet.pause` | `POST /agents/:id/pause` | `scope` — ours is fleet-wide (the loop stops pulling new orders and the run in flight finishes); upstream's is per-agent and also cancels that agent's active heartbeats |
| `M-CTL-3` | the symmetric half of M-CTL-2, on `resume` | `fleet.resume` | `POST /agents/:id/resume` | `scope` — ours lets the loop pull orders again; upstream's resumes one agent |
| `M-CTL-4` | graceful `stop` vs the **irreversible** terminate | `fleet.stop` | `POST /agents/:id/terminate` | `effect` — ours is graceful and reversible (`fleet.start`/`restart` brings the loop back); upstream's terminate is irreversible |
| `M-CTL-5` | the broadest local stop vs the irreversible per-agent terminate | `fleet.halt` | `POST /agents/:id/terminate` | `scope+effect` — the scope (whole directive loop vs one agent) and the effect (reversible vs irreversible) both diverge |
| `M-CTL-6` | the whole loop vs **one run's** cancel | `fleet.kill` | `POST /heartbeat-runs/:runId/cancel` | `scope` — upstream cancels one named heartbeat run; ours takes the loop down, and its handler releases the in-flight run's claim — a superset of one run |
| `M-CTL-7` | nudge a worker: a control message vs `wakeup` | `fleet.poke` | `POST /agents/:id/wakeup` | `scope` — correspondence by concept, not by name: ours writes a control message the loop acks on its next poll; upstream's wakes one agent so a heartbeat runs |
| `M-CTL-8` | batch sweep vs a single run's cancel | `recover.sweep` | `POST /heartbeat-runs/:runId/cancel` | `scope` — ours reclaims every orphaned session whose beat passed the TTL in one pass; upstream cancels one named run per call |
| `M-CTL-9` | closure vs a mutable issue write | `closure.close` | `PATCH /issues/:id` | `effect` — ours closes a work item against the closure invariants and is irreversible in the append-only rail; upstream's is a mutable field write on the same issue, and the same endpoint can reopen it |

**The `UNMAPPED` half** is 33 of the 49 verbs. RC-6 records each of them as its
own row carrying its own `why` — an unmapped verb is an explicit row, never
silence. Restating 33 rows here would mirror the module rather than add a fact to
it, so they appear as **one aggregate row** that names the coverage:

| # | Coverage | Where the per-verb rows live |
|---|---|---|
| `UNMAPPED` | 33 verbs — `fleet.*` (7: `cron`, `live`, `attach`, `start`, `restart`, `refresh`, `update`), `channel.*` (11), `board.*` (8), `recover.*` (4), `closure.*` (3) | `integrations/paperclip/control_mapping.py` — one row per verb, each naming the nearest upstream route when one is close enough to be worth refuting |

**What this is not.** A row names a route; naming one is **not** a licence to call
it. RC-6's module implements no method that mutates upstream — its only transport
call is `GET /api/openapi.json`, the peer's own declaration of its surface, checked
offline through the seam's `FixtureTransport` — so the mapping adds no caller and
no capability. Import direction obeys ADR-0016, and the upstream route inventory is
cited **by path** (`server/src/routes/agents.ts`, `server/src/routes/dashboard.ts`,
and the seam's own `integrations/paperclip/client.py`), never vendored (GR-10, NG4).

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

## 7. Contract 4 — the diagrams blueprint signal (read-only `evidence[]`)

Frozen by [ADR-0017](decision-records/ADR-0017-diagrams-authority-on-the-operator-surface.md)
(issue #463, EPIC #461). Additive: §1–§6 are unchanged, and this section **adds no
facet** — the closed set in §3.1 stays `lessons` / `raid` / `budget`.

**Fleet-side producer:** the projection adapter `integrations/paperclip/diagrams.py`
on the canonical transport ([ADR-0016](decision-records/ADR-0016-paperclip-boundary-single-module.md),
#465), consuming the `kushin77/diagrams` blueprint state this repo declares through
its SSOT membership (`architecture.yaml` + the `gdc-manifest.yaml`
`diagrams.blueprint` pin, #464).
**Fleet-side consumer:** a ticket's structured `evidence[]` (§3.1, ADR-0014 rule 4).
**Authority:** *none taken* — the blueprint is **read-only input**. The ticket stays
the single join node (ADR-0014) and the fleet stays authoritative for dispatch,
claims, budgets and audit (ADR-0012).

**Which question the blueprint owns.** Per CMR `ADR-0018`
(`vendor/CMR/docs/decision-records/ADR-0018-architecture-ssot-transition.md`), the
diagrams blueprint is the single source of truth for **"what is live, today?"** —
and nothing else. *"What did we decide, and why?"* stays with `docs/ARCHITECTURE.md`
+ `docs/decision-records/`; *"who acts?"* stays with the ticket. The projection is
therefore **read-only and one-way**: diagram → decision → ticket; **never**
diagram → write. A drift the blueprint detects is a signal to open an ADR or a
ticket, never a silent absorption.

**The seam shape (ADR-0017).** A diagram signal (a drift Finding, a
rendered-blueprint reference, a `content_hash`) rides `evidence[]` as an additive
structured receipt `{kind, ref, result, checks}` — the same v2 receipt §3.1 froze —
and **not** a new facet, **not** a new `kind`. This leaf **does not touch the
closed `facets` set**: `lessons` / `raid` / `budget` remain its only members,
because `evidence` is explicitly *not authority-tracked* ("appended by whichever
lane ran the proof", §3.1), which is what a read-only projection requires. A drift
finding is a ticket of `kind: "task"`; the Finding is its `evidence[]`. Adding a
facet would be a contract change that must move
`scripts/check-paperclip-integration.sh` (and the schema) with it — so a new facet
is never added by prose, and this section adds none.

| Seam element | Shape | Producer | Authority |
|---|---|---|---|
| diagram signal | `evidence[]` receipt `{kind, ref, result, checks}` | `integrations/paperclip/diagrams.py` (#465) | **none** — appended proof, not authority-tracked |
| drift finding → work item | a ticket `kind: "task"` | the blueprint's drift→decide loop | the ticket stays the join (ADR-0014) |

