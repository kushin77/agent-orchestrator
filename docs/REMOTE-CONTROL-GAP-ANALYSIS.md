# Remote-control gap analysis — our operator control surface vs the paperclip.ing control plane

Issue: [#360](https://github.com/kushin77/agent-orchestrator/issues/360) ·
Retrieved: **2026-09-14** · Base: `origin/master` `ffb2688`

## Why this doc exists

`docs/FLEET-DASHBOARD-GAP-ANALYSIS.md` measured what a **terminal** cannot show and
`docs/PAPERCLIP-ING-GAP-ANALYSIS.md` measured which **capability families** upstream
already covers. Neither measured the thing that decides whether a remote operator can
*act*: the **control verb and its reach**. This doc does exactly that — it inventories
the verbs we ship today, records for each one how far the verb actually travels
(a local PID, a local tmux server, a local crontab, or a real network call), inventories
upstream's control verbs from its own source, and then states the gap as a single,
dependency-ordered work breakdown for a **remote control command center CLI**.

It is a **measurement**, not a proposal dressed as one. Where we have no evidence, §7
says so plainly instead of asserting a capability.

## 1. Method, and what "reach" means

- **Upstream evidence** was fetched read-only from `paperclipai/paperclip` (MIT,
  default branch `master`, 8,079 tracked paths) with
  `gh api repos/paperclipai/paperclip/contents/<path>`. Every upstream path cited below
  is a path in that repository; the file was opened, not inferred. Upstream paths are
  cited in backticks and never as links — they are foreign files.
- **Our evidence** is read from this repo's `master` tree in an isolated lane worktree.
- **Reach** is the honest, load-bearing axis. A verb's reach is *local-process* if it
  takes effect by signalling a PID on this host, *local-tmux* if it needs the tmux
  server on this host, *local-config* if it edits this host's crontab, *local-filesystem*
  if it only moves a file the rungs read, *loopback-HTTP* if it is an HTTP route that
  binds `127.0.0.1`, or *remote* if it genuinely crosses a host boundary. A verb that
  only works when the operator is on the machine is **not** a remote control, no matter
  how expressive it is.

### 1.1 The one-line finding

Every control verb this repo ships is **local-process, local-tmux, local-config or
local-filesystem**; every HTTP surface this repo ships is **GET-only and bound to
loopback**; and the fleet↔upstream seam is a **read/projection seam with a single narrow
outbound write (ticket push) and no inbound path at all**. The control gap is therefore
not "our verbs are worse" — several are more precise than upstream's — it is that
**none of them can be commanded from off the machine, and nothing outside the machine
can reach them.**

## 2. Our control surface, inventoried from source

### 2.1 `fleet/control.py` — the human-override terminal (18 verbs)

`fleet/control.py` is a single argparse dispatch over 18 verbs; the registry is an
explicit tuple in `build_parser()`. Reach is taken from the implementation, not the
docstring.

| Verb (`python3 fleet/control.py …`) | What it does | Reach |
|---|---|---|
| `start` | starts the rungs that are missing (`brain`, `sister`, `monitor`) | **local-process** — `watchdog.spawn` |
| `status` | rungs, pause/stop flags, tracked runs | **local-filesystem** — reads `.fleet/*.heartbeat.json`, `.fleet/runs/` |
| `refresh` | `git pull --ff-only` + board snapshot + `make verify` | local-git + `gh` network read |
| `update` | `refresh` + rebuild the knowledge index | local |
| `poke` | pings the sister; it acks (liveness) | **local-filesystem + local-process** — writes `.fleet/control-<hex>.json`, signals the loop |
| `pause` | holds the queue (the in-flight run finishes) | **local-filesystem** — a flag file the loop reads |
| `resume` | pulls the next order again | **local-filesystem** |
| `stop` | exits after the current run, cleanly | **local-filesystem + local-process** |
| `kill` | terminates the run, releases its claim, escalates | **local-process** — `os.kill(pid, signum)` against the loop pid read from `.fleet/sister.heartbeat.json` |
| `restart` | re-execs the same code (fast) | **local-process** — `SIGTERM` then re-exec |
| `halt` | stops the fleet | **local-process** |
| `override --issue N` | forces `#N` past a live claim | local + `gh` claim-ledger writes |
| `debug [--tail N]` | full non-destructive state dump | **local-filesystem** |
| `watch` | idle-watches the slog | **local-filesystem** |
| `health [--stale-minutes N]` | tri-state signal: 0 healthy / 1 degraded / 2 failing | **local-filesystem** |
| `cron <sub>` | passthrough to `fleet/cron.py install\|status\|run\|respawn\|disable\|enable\|uninstall` | **local-config** — this host's crontab |
| `live` / `attach [--dry-run]` | ensures the rungs, then `tmux attach -t fleet` | **local-tmux** |

Two implementation facts matter for the proposal:

1. **The signal path is deliberate and documented.** `_loop_pid()`'s docstring in
   `fleet/control.py` explains why a mailbox message alone is insufficient: *"the loop is
   blocked in the child run while a task is in flight, so a control message would sit
   unread until the task ends. Signals are how a caller takes effect immediately."* A
   remote control channel inherits that constraint — it cannot be a maildrop.
2. **`_send_control()` is a loopback-only mailbox.** It writes a JSON message to a
   `control-<hex>.json` path and hands it to `fleet/channel.py send`, which queues it into
   `FLEET_DIR/inbox`. Both endpoints are directories on this filesystem.

### 2.2 `fleet/channel.py` — the steering transport (13 verbs)

`fleet/channel.py` is the normative mailbox: `verify`, `send`, `status`, `report`,
`escalate`, `listen`, `wait`, `watch`, `brain-inbox`, `brain-outbox`, `order`,
`consume`, `head-commit`. The mailbox directories are declared at module level —
`.fleet/inbox`, `.fleet/sent`, `.fleet/outbox`, `.fleet/brain/{inbox,outbox}`,
`.fleet/done` — with `.fleet/slog.jsonl` as the append stream. `fleet/CONTRACT.md` §0
fixes the authority split: the contract is authoritative for roles and vocabulary, the
schema for the envelope, and `channel.py` *"is authoritative for what is actually
enforced (it refuses invalid traffic)"*. Reach: **local-filesystem**, and by design
(ADR-0011 chose *"file mailbox (localhost)"* as the transport, with A2A as the
graduation target). One validator is a genuine trust boundary and is worth naming:
`cmd_order` *"refuses an operator sender, so this is the only way in"* — the transport
already carries a refusal rule, which the remote channel must not weaken.

### 2.3 Governance CLIs — the ledger, reconciliation and lifecycle verbs

| Surface | Verbs | Reach |
|---|---|---|
| `governance/dispatch/cli.py` | `audit`, `eligible`, `claim`, `release`, `status`, `held`, `reap`, `snapshot` | **local-filesystem** ledger (`.board/locks/`, claim events); `snapshot --from-github` is a `gh` network read |
| `governance/reconcile/cli.py` | `stamp`, `clear`, `status`, `sweep`, `watch` | **local-filesystem** (`.fleet/sessions/`) |
| `governance/lifecycle/cli.py` | `audit`, `status`, `close`, `collect` | **local-filesystem** + `gh` (merge/close/branch facts) |
| `governance/knowledge/cli.py` | `build`, `validate`, `query`, `coverage` | **local-filesystem** |
| `governance/board/cli.py` | `check`, `exceptions`, `export`, plus a check sub-command | **local-filesystem** (reads a snapshot; the detector is offline and stdlib-only by contract) |

None of these is reachable as a network verb. `claim`/`release` — the fleet's real
mutex — are `python3 governance/dispatch/cli.py` invocations on this host.

### 2.4 The HTTP surfaces we do ship — all GET-only, all loopback

The repo does own an HTTP server: `portal/server/httpd.py` binds
`serve(app, host="127.0.0.1", port=8787)`. Its route table lives in
`portal/server/app.py`, and **every fleet-relevant family it serves is explicitly
GET-only**, with the refusal visible in the source:

- `_route_fleet` — *"the fleet surface is GET only"* (405 otherwise); the projection it
  serves is `fleet/console.py`'s `snapshot()`, consumed by `portal/server/fleet.py`,
  which states its own rule: *"Cannibalize, do not duplicate"* — it imports the console
  and *"re-implements no reader"*.
- `_route_portal` — *"the portal-surfaces feed is GET only"*.
- `_route_finops` — *"the finops surface is GET only"*.
- `_route_live_feed` — *"the live feed is GET only"*; `docs/LIVE-DATA-BRIDGE.md` records
  the same for the whole `/api/v1/bridge/*` family (*"All endpoints are `GET`"*).
- `_route_ops` — *"the ops surface is GET only"*.
- `_route_bridge` — *"the live-data bridge is GET only"*.

The only `POST`s in the whole route table are `console/login`, `console/logout` and the
conversational surface's conversation/turn routes (ADR-0023) — session and chat, not
fleet control. And every one of these families is **flag-gated ON by default** (AO-GR-6):
`infra/feature-flags/registry.yaml` declares carriers for
`fleet_projection`, `portal_surfaces`, `finops_reports`, `telemetry_live_feed`,
`ops_health`, `live_bridge`, `telemetry_exposition`, `fleet_health_export` — each
`default: on`.

### 2.5 The one outbound network write we own

`fleet/health_publish.py` is the single place our fleet deliberately speaks to a remote
plane: `plan` renders and sends nothing, `push` sends an OTLP/HTTP JSON payload, and
push *"refuses unless the surface's feature flag is ON and an endpoint is configured"* —
the producer is *"inert when unconfigured"*. Reach: **remote, outbound, telemetry only**.
It carries no command and accepts none.

### 2.6 Verdict on our surface

| Dimension | Measured state |
|---|---|
| Verbs available locally | Rich — 18 in `fleet/control.py`, 13 in `fleet/channel.py`, 22 across the four governance CLIs |
| Verbs available remotely | **Zero.** No verb is reachable over a network; the HTTP server binds loopback and refuses every mutation on the fleet path |
| Transport for a control message | Local filesystem mailbox + `os.kill` signals (ADR-0011), by explicit decision |
| Audit of a control action | Present and strong (`fleet/channel.py` `.fleet/slog.jsonl`; the hash-chained `telemetry/ledger/`) but **local** |
| Caller identity | The local shell. There is **no** caller identity on the control path to authenticate |

## 3. The paperclip control surface, inventoried from upstream

### 3.1 The upstream stance

`DESIGN.md` states it directly: *"Paperclip is an operational control plane: org charts,
tasks, heartbeat runs, budgets, approvals, audit logs. The user is an operator scanning
state and making decisions."* Its status vocabulary is systematic — *"running / paused /
blocked / awaiting-approval / over-budget map to a single semantic status token set used
identically everywhere"*. `ROADMAP.md` lists shipped operator-control milestones
including **Better Budgeting** (*"safer hard stops, and better operator control over how
autonomy turns into real cost"*), **Agent Reviews and Approvals**, **Activity log &
action attribution** and **Enforced Outcomes (watchdogs, recovery actions, review
gates)**.

### 3.2 Control verbs, from the upstream route files

Every row below is a real route registration in the cited upstream file.

| Capability | Upstream route | Audit action recorded |
|---|---|---|
| Pause an agent (stops heartbeats) | `POST /agents/:id/pause` — `server/src/routes/agents.ts` | `agent.paused`; also calls `heartbeat.cancelActiveForAgent(id)` |
| Resume an agent | `POST /agents/:id/resume` — same file | `agent.resumed`; refuses `409` when `orgChainHealth.status === "invalid_org_chain"` rather than resuming a broken chain |
| Clear an error state | `POST /agents/:id/clear-error` — same file | `agent.error_cleared` |
| Approve a hire | `POST /agents/:id/approve` — same file | `agent.approved` |
| **Terminate** an agent (irreversible) | `POST /agents/:id/terminate` — same file | `agent.terminated`, with `details.cancellation.{runsCancelled,wakeupsCancelled}` and the descendant list it invalidated |
| Delete an agent | `DELETE /agents/:id` — same file | `agent.deleted` |
| Mint / revoke agent API keys | `POST` / `DELETE /agents/:id/keys[/:keyId]` — same file | `assertBoard(req)` gate |
| Wake an agent / invoke a heartbeat | `POST /agents/:id/wakeup`, `POST /agents/:id/heartbeat/invoke` — same file | — |
| Cancel a running heartbeat | `POST /heartbeat-runs/:runId/cancel` — same file | — |
| Resolve a runtime request | `POST /heartbeat-runs/:runId/runtime-requests/:requestId/resolve` — same file | — |
| Record a watchdog decision | `POST /heartbeat-runs/:runId/watchdog-decisions` — same file | — |
| **Tool-call / provider tracing** | `GET /heartbeat-runs/:runId/provider-trace`, `/events`, `/log`, `/workspace-operations`; `.../provider-trace/download` — same file | read-only |
| Roll back an agent config revision | `POST /agents/:id/config-revisions/:revisionId/rollback` — same file | read/rollback pair |
| Reset a task session | `POST /agents/:id/runtime-state/reset-session` — same file | — |
| Org chart (live) | `GET /companies/:companyId/org`, `/org.svg`, `/org.png` — same file, rendered by `server/src/routes/org-chart-svg.ts` | read-only |
| Approvals (hire, top-up, strategy) | `server/src/routes/approvals.ts` — list / get / create / `approve` / `reject` / `request-revision` / `resubmit` / comments | decision notes are part of the object |
| **Immutable audit trail** | `server/src/routes/activity.ts` — `GET /companies/:companyId/activity`, `/audit/agent-actions`, `/audit/agent-actions.csv` | `docs/api/activity.md`: *"The activity log is append-only and immutable."* |
| Budgets with a hard stop | `docs/api/costs.md` — `GET /costs/summary|/by-agent|/by-project`; `PATCH /companies/:companyId` and `PATCH /agents/:agentId` set `budgetMonthlyCents`. Enforcement table: **80 % soft alert**, **100 % hard stop — agent is auto-paused** | spend is a first-class record |
| Goals and projects | `server/src/routes/goals.ts`, `docs/api/goals-and-projects.md` — goal hierarchy company → team → agent, projects with workspaces | — |
| Issue tree holds / previews | `server/src/routes/issue-tree-control.ts` — `POST /issues/:id/tree-control/preview`, `POST /issues/:id/tree-holds`, `GET /issues/:id/tree-control/state` | `issue.tree_control_previewed` |
| Decision queues | `server/src/routes/decisions.ts` (`POST /decisions/:id/decide`, `/dismiss`, `/cancel`) and `server/src/routes/decision-queues.ts` | — |
| Watchdog / dispatch / wake modules | `server/src/modules/active-run-watchdog/**`, `server/src/modules/run-dispatch/**`, `server/src/modules/wake-queue/**` | — |
| Dashboard | `server/src/routes/dashboard.ts` — `GET /companies/:companyId/dashboard` | read-only |
| Runner authority model | `packages/paperclip-runner/SEMANTIC_ACTIONS.md` — a **frozen, versioned** action catalog whose dispatcher *"project[s] only actions that have current actor, task, company, claim, mode, and application-binding authority"* and *"rechecks that authority before each call"*; mutations additionally require *"an atomic idempotency store"* | — |

### 3.3 The upstream CLI is a control CLI, not only a read CLI

This is the single most load-bearing upstream fact for the proposal, because the public
doc undersells it. `docs/cli/control-plane-commands.md` documents `issue`, `company`,
`agent list|get`, `skills`, `approval`, `activity`, `dashboard`, instance settings and
`heartbeat run`. The **implementation** is wider: `cli/src/commands/client/agent.ts`
registers agent lifecycle commands, and `cli/src/__tests__/agent-lifecycle.test.ts`
proves, verb by verb, the endpoint each one hits:

```
await run(["agent", "pause", AGENT_ID]);     -> ["POST", ".../api/agents/<id>/pause"]
await run(["agent", "resume", AGENT_ID]);    -> ["POST", ".../api/agents/<id>/resume"]
await run(["agent", "approve", AGENT_ID]);   -> ["POST", ".../api/agents/<id>/approve"]
await run(["agent", "terminate", AGENT_ID]); -> ["POST", ".../api/agents/<id>/terminate"]
await run(["agent", "heartbeat:invoke", AGENT_ID]);
await run(["agent", "config-revision:rollback", AGENT_ID, REVISION_ID]);
await run(["agent", "runtime-state:reset-session", AGENT_ID, "--task-key", "task-1"]);
```

So upstream ships an operator CLI that can **pause, resume, approve, terminate, roll
back and re-session** a worker over HTTP — and an operator-facing web UI beside it. Our
control verbs and upstream's are *the same kind of thing*; only the reach differs.

### 3.4 Upstream's authority discipline, which we should copy rather than invent

`packages/paperclip-runner/SEMANTIC_ACTIONS.md` describes a control plane that is
stricter than ours in four specific ways worth carrying into the design:

1. **Authority is rechecked before each call**, not granted once at session start.
2. **The catalog is descriptive, not granting** — *"Importing it or finding an operation
   in it does not grant permission to show or call that operation."*
3. **Mutations require an atomic idempotency store** with a recovery path for a mutation
   that succeeds before its receipt fails — i.e. **exactly-once control**, not
   at-most-once.
4. **Receipts never carry raw tool content** — a digest plus allowlisted references only.

## 4. The gap

Legend for the middle columns: **[L]** = we have it, local only; **[∅]** = we have no
equivalent; **[S]** = we have it and it is already wired to the seam.

| # | Capability | Ours today | paperclip | The gap | Who owns closing it |
|---|---|---|---|---|---|
| 1 | **Remote control reach** | 18 local verbs; zero network verbs | every control verb is an HTTP route + a CLI sub-command | **[L]** — no verb crosses a host boundary. This is the epic | control-plane / portal lane |
| 2 | **Inbound command channel** | none; `fleet/channel.py cmd_order` *"refuses an operator sender"* | `POST /api/…` on every control family | **[∅]** — nothing outside this host can issue a command *at all* | control-plane / portal lane |
| 3 | **Caller identity on the control path** | the local shell; no caller object | agent key/JWT, board token, session cookie; `assertBoard(req)` on every mutation; company-scoped `403` | **[∅]** — there is nothing to authenticate because there is no channel | identity-rbac lane |
| 4 | **Pause/stop semantics** | `pause`/`resume`/`stop`/`halt` — rung- and queue-level, local flags | `pause`/`resume` per agent; `DELETE`-free, reversible; `cancel` per run | **[L]** — our verbs are *coarser* (fleet-wide) and *local*; upstream is per-agent and remote | control-plane / portal lane |
| 5 | **Terminate with blast-radius accounting** | `kill` = `SIGTERM` to one loop pid; `prune.py` reclaims | `terminate` cascades to descendants and reports `{runsCancelled, wakeupsCancelled}` | **[∅]** — we have no cascade accounting and no irreversible-verb confirmation | autonomous-ops lane |
| 6 | **Hard budget stop** | `gateway/finops/budget.py` `hardCapPct`; `telemetry/budgets/config/killswitch.yaml` | 80 % soft alert, **100 % hard stop, agent auto-paused** | **[L]** — the rail exists; the *automatic remote action* and the per-agent scope do not | telemetry lane |
| 7 | **Per-agent budget scope** | tenant/vendor scope (`VendorBudgetCap`, `window: month`) | cap per agent / team / project | **[∅]** — `scope.level = agent` has no fleet producer | telemetry lane |
| 8 | **Approvals as objects** | a claim gate (`governance/dispatch`) and `fleet/directive.json`; no approval record | first-class approval with payload, decision note, re-submit, comments | **[∅]** — "a top-up is an approval" is a *convention* here, not a record | autonomous-ops lane |
| 9 | **Live org chart / reporting lines** | declarative only: `registry/personas/cards/`, `registry/packs/releases/` | live `org` + `org.svg` + `org.png` from the agent records' `reportsTo` | **[∅]** — our org is a declaration, never a projection | registry lane |
| 10 | **Goal object with ancestors** | epic/chain markers in issue bodies and `.board/snapshot.json`; `governance/ticket/` projects them | `goals` / `projects` records with `ancestors` | **[S]** — the ticket-contract v2 projection already carries `goal` (ADR-0014) | autonomous-ops lane |
| 11 | **Audit of a control action** | `.fleet/slog.jsonl` + hash-chained `telemetry/ledger/` | append-only, immutable activity log per mutation | **[L]** — the audit exists and is *stronger* than upstream's on tamper-evidence, but it records only what a local process did | telemetry lane |
| 12 | **Run / step tracing** | per-rung `.fleet/*.log`, `.fleet/runs.jsonl`, `.fleet/telemetry.jsonl` | `provider-trace` frames + `/events` + `/log` + downloadable trace | **[L]** — we have traces, no remote reader and no per-call frame model | telemetry lane |
| 13 | **Watchdog** | `fleet/watchdog.py` — stall/loop/death, respawn verification, `RESPAWN FAILED`; cron-owned | `active-run-watchdog` module + `POST /watchdog-decisions` | **[L]** — the detection is arguably finer; it is entirely on-host | autonomous-ops lane |
| 14 | **Refusal when the plane is unreachable** | `fleet/health.py` tri-state `0/1/2`; `fleet/health_publish.py` refuses with no endpoint | typed errors (`400/401/403/404/409/422/503`) + `409` "another agent owns the task. **Do not retry.**" | **[L]** — we have the honesty contract but no remote caller to refuse *to* | control-plane / portal lane |
| 15 | **Remote read surface** | `portal/server/*` — GET-only, **bound `127.0.0.1:8787`**, all flags OFF | full REST + UI + CLI | **[L]** — the read half exists and is proven; it is loopback-only and dark by default | portal lane |
| 16 | **Tool / capability governance** | `guardrails/policy/controls.yaml` + `gateway/mcp/`; `portal/server/controls.py` maps a toggle to the action it gates | skills + tool gateway + the frozen semantic-action catalog with per-call recheck | **[L]** — the vocabulary exists, the run-scoped recheck does not | guardrails lane |
| 17 | **Control-plane seam** | `integrations/paperclip/**` — a **read/projection seam**, plus one outbound ticket push | the control plane itself | **[S]** for reads; **[∅]** for control — see §5 | paperclip boundary lane |
| 18 | **Idempotent control** | `governance/dispatch` claims are single-writer with `release`/`reap`; no command idempotency for control verbs | mutations require an atomic idempotency store | **[∅]** — a retried remote `pause` must not be a second effect | control-plane / portal lane |

### 4.1 The three-way classification the brief asked for

- **(a) We have it locally, but not remotely** — rows 1, 4, 6, 11, 12, 13, 14, 15, 16.
  This is the *bulk* of the gap and the reason the EPIC is a **transport + identity +
  audit** epic, not a features epic.
- **(b) We have no equivalent** — rows 2, 3, 5, 7, 8, 9, 18. Four of these (2, 3, 5, 18)
  are on the critical path for a remote control center; three (7, 8, 9) are genuine
  product gaps that the remote CLI will *expose* rather than create.
- **(c) We have it and it is already wired to the seam** — row 10 (and row 17's read
  half). The ticket contract v2 (ADR-0014, #400) already projects `goal` and a derived
  `status`, so the remote CLI can read a ticket's standing without inventing a store.

## 5. The seam as it stands — and therefore what the control gap *is*

The claim in the brief was that the existing seam is a **projection/read seam**. That
claim is true, and it is provable in code rather than by reading prose.

**Proof 1 — the route surface is defined as the read surface.** `integrations/paperclip/api/surface.py`:

> *"A method that needs arguments (a mutation) is skipped: this is the *read* surface.
> The result is deterministic — sorted by `(method, path)`."*

That module builds the committed `openapi.json` by *introspecting*
`integrations/paperclip/client.py` and invoking each **argument-free** public method
against a recording transport — *"a second, hand-written route list is exactly the
parallel description the lane forbids."* The document therefore cannot describe a route
the adapter does not call, and by construction it describes **only reads**: `/api/health`,
`/api/openapi.json`, and `/api/companies/{companyId}/{agents,issues,costs,approvals,activity,dashboard}`.

**Proof 2 — the client declares reads first and mutations under a separate heading.**
`client.py` groups them explicitly: `health`, `openapi`, `agents`, `issues`, `costs`,
`approvals`, `activity`, `dashboard` under the read methods, then a comment —
`# -- mutating (carry X-Paperclip-Run-Id through the seam) ----` — introducing only
`create_issue`, `update_issue` and `request_topup`.

**Proof 3 — exactly one of those mutations is ever called.** `integrations/paperclip/cli.py`
`cmd_push()` is the only caller: it builds the plan, calls `client.health()`, then loops
`client.create_issue(ticket)` once per ticket. There is no call to `update_issue` or
`request_topup` anywhere in the tree outside tests. So the seam's total outbound write
is **"create one upstream issue per fleet ticket"**, and its dry-run mode prints the
count without sending.

**Proof 4 — there is no inbound path.** Nothing in this repo receives a request *from*
upstream: the seam's transports are `HttpTransport` (outbound `urllib.request`) and
`FixtureTransport` (canned fixture, used by every test and by the gate), and the only
inbound HTTP listener in the repo is the loopback console. There is no webhook route, no
upstream-authenticated endpoint, and no `integrations/paperclip/**` code that parses an
inbound request body.

**Therefore the control gap is exactly three things**, and naming them precisely is what
makes the EPIC filable:

1. **No inbound channel exists.** Upstream can be *read* from here; it cannot *command*
   anything here, because nothing here accepts a command.
2. **Our control verbs have no remote form.** They are correct and well-audited, but
   their entire effect is delivered by `os.kill`, by a file in `.fleet/`, or by
   `tmux attach` — none of which survives leaving the host.
3. **Upstream's control verbs have no mapping.** We have no closed vocabulary that says
   "our `pause` is upstream's `POST /agents/:id/pause`", so a remote command center
   cannot be built from the seam as it stands; the seam must first gain a *control*
   half beside its read half.

The seam is also gated, which is good news for the proposal: `scripts/check-paperclip-integration.sh`
runs in `make verify` as the check named `paperclip-integration` (`scripts/verify.sh`),
so contract drift in the seam is already a red gate rather than a convention.

## 6. The boundary that must not be broken

The proposal below is only legitimate if it respects four standing rules. Each is quoted
from the artifact that owns it, so the constraint is checkable rather than remembered.

1. **NG4 — never do another repo's work.** `docs/CROSS-REPO-EXECUTION-BOUNDARY.md` §1:
   *"A repo remediates compliance findings for itself only"* … *"A finding about another
   repo is resolved by filing a **direction issue on that repo's board** — never by an
   edit, a settings change, or a pull request authored from here."* §3 adds the
   **report-is-read-only rule**: a report is *"output, not a control surface"*.
   **Consequence:** the remote control center commands **this** fleet. Anything upstream
   `paperclipai/paperclip` must change (a missing endpoint, a coarse refusal) is a
   **direction issue on that repository's board** — filed and reported, never patched
   from here. This document itself edits only this repo.

2. **No second dashboard.** `docs/decision-records/ADR-0022-telemetry-exposition-authority-split.md`
   carries the binding refusal — *"**build a second dashboard**. The human operator
   surface is the **web single pane of glass** … No `dashboards/` directory, no Grafana
   JSON, no bundled Grafana"* — and states the split: the monitoring plane is the
   **machine** surface, the SPoG is the **human** surface, and *"re-deriving either from
   the other is the failure this EPIC exists to prevent."*
   **Consequence:** the remote control center is a **CLI**, not a UI. It must not grow a
   frame, a filter bar or a timeline; it emits text and structured JSON for a human or a
   script. Any new view of fleet state obeys ADR-0022's split, and
   `docs/FLEET-DASHBOARD-GAP-ANALYSIS.md` records that the remote human surface is
   **sourced from paperclip.ing, not built here** — the greenfield build was
   superseded by EPIC #359.

3. **A signal drives a ticket, never a silent action.** ADR-0022 refusal 4:
   *"A signal drives a ticket, never a silent action. The single join node is the ticket
   (ADR-0014). The direction is one-way and permanent: **signal → ticket → human**.
   Nothing in the monitoring path writes fleet state — no `fleet/**` file, no `.fleet/**`
   record, no claim, no branch, no worktree is mutated by an export."* `docs/OBSERVABILITY.md`
   restates it in its own table.
   **Consequence, and it is the sharpest design constraint in this document:** the remote
   CLI has two *different* kinds of verb and must never blur them. A **monitoring** verb
   (read a signal, detect a threshold) may only **file a ticket**. A **control** verb
   (pause, stop, terminate) is a *deliberate operator act* — it is explicitly *requested
   by a human*, carries a caller identity, and is the only thing permitted to write fleet
   state. "The health check noticed X, so it paused the fleet" is precisely the silent
   action this rule forbids.

4. **One authority per concept; one home per module.** `ADR-0012` (Hermes/Paperclip
   ownership boundary) fixed the operating rule *"map the policy, do not couple the
   runtime"*. `ADR-0013` chose **adopt the upstream CLI over its HTTP API** — *"a process
   boundary, not a fork and not an embed"* — and names what stays ours *"regardless of
   mode"*: the terminal TUI `fleet/console.py`, the claim ledger `governance/dispatch/`,
   and the FinOps tiers. `ADR-0016` fixed **one home** for the boundary adapter
   (`integrations/paperclip/**`) and states the dependency direction explicitly:
   `adapters/**` may import the seam, **the seam must never import `adapters/**`**.
   `ADR-0015` fixes routing's single authority.
   **Consequence:** the remote command center CLI is a **new, separate** artifact — it is
   *our* operator CLI, not the paperclip boundary adapter. It **imports**
   `integrations/paperclip/client.py` rather than re-implementing a client (so no second
   module for the boundary appears), and it must not import or restate the routing
   dialect, the claim ledger or the FinOps tiers. `scripts/check-paperclip-canonical-module.sh`
   (wired as `paperclip-canonical-module`) already fails by name on a second top-level
   module for the boundary, so the guard for this exists.

5. **Flag-gated ON by default, no Actions, no secrets, no ad-hoc apply.** AO-GR-6 with
   `infra/feature-flags/registry.yaml` `default_policy: on`; GR-15 (automation is
   code-native — `make` targets and cron, never a workflow file); GR-6 (endpoints and
   tokens from the environment or a secret manager, never committed).

## 7. What could not be evidenced

Stated plainly rather than smoothed over:

- **No live upstream instance was contacted.** Everything about upstream is read from its
  repository at the current `master`. The behavioural claims (that `pause` really stops
  heartbeats, that the activity log really is immutable, that the 100 % budget stop really
  auto-pauses) are the *source's own* documented and coded intent, cross-checked between
  the route implementation, the API docs and `DESIGN.md`. That is strong evidence, not
  execution evidence. Standing the process up and exercising the verbs end-to-end is
  adoption work, and ADR-0013 says so itself.
- **Upstream's `requiresAuth`/`assertBoard` matrix is only partially evidenced.** I opened
  the gate calls on the routes I cited (`assertBoard(req)`, `assertCompanyAccess(req,
  companyId)`) and the auth model in `docs/api/overview.md`, but I did not enumerate every
  route's guard. The claim "every mutation is board-gated" is therefore evidenced for the
  agent-lifecycle and approval routes, and *not* asserted for the full surface.
- **`docs/api/dashboard.md` and the root `adapter-plugin.md` came back empty** from the
  contents API (0 bytes). I did not use them and make no claim that depends on them.
- **The exact upstream CLI surface for the *non*-agent families** is evidenced from
  `docs/cli/control-plane-commands.md`, which is the shipped doc — but the doc and the
  implementation already disagree for the agent family (§3.3), so the CLI surface should
  be treated as **understated by the docs** in general, not as exactly known.
- **No measurement of our fleet's live state** was made. This lane must not touch
  `.fleet/`, tmux or any running process, so every "reach" claim above is derived from
  source (which call site delivers the effect), not from observing a running fleet.
- **Cost/latency of the proposed channel is not measured.** A `make verify`-scale
  measurement of the control path does not exist yet; the EPIC's children each carry a
  budget-shaped acceptance instead of a number this doc cannot honestly produce.

## 8. Proposed EPIC and children — the remote control command center CLI

### 8.1 The EPIC

**Title:** Remote control command center CLI — a reachable, identified, audited control
channel for the fleet.

**Definition of done (the EPIC's own acceptance shape):** an operator on a machine other
than the fleet host can (a) *see* the fleet's standing, (b) issue a named control verb
from a **closed, declared vocabulary**, (c) have that verb carry a **caller identity**,
(d) have it produce **exactly one effect and exactly one audit record**, and (e) receive
an **explicit refusal** — never a silent no-op — when the plane is unreachable, when the
verb is not permitted for that caller, or when the command was already applied.

**Non-goals, stated so a child cannot drift into them:** no new dashboard or UI
(ADR-0022); no second knowledge store or second ledger (ADR-0012/0015); no upstream code
edited (NG4); no workflow file (GR-15); nothing shipped without owner review, ON by default per AO-GR-6.

### 8.2 The children, dependency-ordered

One issue = one lane = one branch, no two lanes sharing a file in a wave. `Blocked-by`
edges are the chain; the critical path is **RC-1 → RC-2 → RC-3 → RC-4 → RC-5 → RC-8**.

| # | Child | Scope (what it builds) | Lane glob (sole writer) | Acceptance shape | `Verify:` | Blocked by |
|---|---|---|---|---|---|---|
| **RC-1** | **ADR: the remote control transport and the authority split** | The decision record that fixes where the command channel lives, its direction, its auth model, and its relationship to ADR-0012/0013/0016/0022. It must decide **against** an inbound-from-upstream model unless it can name the upstream endpoint, and must state that monitoring verbs file tickets while control verbs are operator-requested. | `docs/decision-records/ADR-00NN-remote-control-transport.md` + its one row in `docs/decision-records/README.md` | Record is `accepted`, names the rejected alternatives (embed a web console; an inbound webhook; a peer-hosted plane), and each standing rule in §6 is cited with the consequence it forces | `bash scripts/check-docs.sh` and `bash scripts/check-authority.sh` | — |
| **RC-2** | **The control-verb vocabulary (one declaration)** | A closed, declarative registry of control verbs: id, effect class (`read` \| `hold` \| `stop` \| `irreversible`), required capability, the audit action it records, the refusal codes it can return, and whether it is idempotent. One file, so a verb cannot be invented anywhere else. | `control-plane/control/verbs.yaml` + `control-plane/control/schema/verbs.schema.json` + its own `scripts/check-control-verbs.sh` | Every verb used by RC-3/RC-5 is declared here or the gate fails **naming the undeclared verb**; a verb with no `effect_class` or with an unknown refusal code fails; the negative control proves it can fail | `python3 control-plane/control/cli.py validate` and `bash scripts/check-control-verbs.sh` | RC-1 |
| **RC-3** | **The control API (server half, flag-gated OFF)** | `POST` routes on the existing console app that translate the closed vocabulary into the **existing local levers** (`fleet/control.py` verbs, `governance/dispatch/cli.py`) by *delegating*, never by re-deriving. AuthN before anything; AuthZ via `identity/rbac`; flag `surfaces.remote_control` OFF. | `portal/server/control_api.py`, its tests, and the **single** route-table hook line in `portal/server/app.py` (this file is RC-3's sole responsibility in this wave) | Every route refuses `405` for a wrong method, `401` with no caller, `403` for an insufficient capability, `409` for a duplicate in-flight command, `422` for a verb outside the vocabulary, `503` when the local lever is unreachable; the flag being off makes the family **invisible** (checked before AuthN), mirroring `docs/LIVE-DATA-BRIDGE.md` | the module's pytest suite + `python3 scripts/check-feature-flags.py` | RC-1, RC-2 |
| **RC-4** | **Exactly-once control, with the audit record and the refusal path** | The one effect / one record rule: a command id is minted and stored so a retried verb is idempotent (upstream's *"atomic idempotency store"* discipline), the audit record is appended to the existing `telemetry/ledger/` rail and the `.fleet/slog.jsonl` stream rather than a new ledger, and the unreachable-plane path **refuses with `503` and a reason** instead of degrading to a no-op. | `portal/server/control_audit.py` + tests, and its own `scripts/check-control-audit.sh` | A replayed command produces **one** ledger record and returns the original receipt; an unreachable lever returns `503` **and writes no record**; a stolen/reordered command id is refused. Negative control provokes each of the three. | `bash scripts/check-control-audit.sh` | RC-3 |
| **RC-5** | **The remote command center CLI (client half)** | `ao-control status\|verbs\|pause\|resume\|stop\|kill\|override\|audit` — a thin client that speaks only the RC-3 API, prints a receipt per action, and **refuses locally** when it cannot verify the plane (no silent success). It **imports** `integrations/paperclip/client.py` for any upstream call rather than re-implementing a transport (ADR-0016). | `control-plane/cli/**` + tests | Every verb maps to exactly one declared command id; the CLI never writes fleet state directly; an unreachable plane exits non-zero with a named reason; a dry-run prints the request it would send and sends nothing | the CLI's pytest suite + `python3 control-plane/cli/main.py --help` | RC-3, RC-4 |
| **RC-6** | **The upstream control-verb mapping (a mapping, never authority)** | A table and module that maps each of our control verbs onto the corresponding upstream route (`POST /agents/:id/pause\|resume\|terminate\|clear-error`, `POST /heartbeat-runs/:runId/cancel`), recording the **mismatch list** — starting with the two known to be irreversible against our model (upstream `terminate` has no fleet counterpart; our fleet-wide `pause` has no per-agent upstream counterpart). Import direction obeys ADR-0016 (`adapters/`-style code may import the seam). | `integrations/paperclip/control_mapping.py` + tests | Every mapped verb names a real upstream route **and** a declared verb from RC-2; an unmapped verb is an explicit `UNMAPPED` row, not silence; the module is exercised offline through `FixtureTransport` (no network in the gate) | the module's pytest suite, run through the offline fixture transport | RC-2 |
| **RC-7** | **The seam doc's §5 mismatch list gains the control rows** | `docs/PAPERCLIP-ING-INTEGRATION.md` §5 gains the control mismatches RC-6 measured, in the same numbered-table form §5 already uses, so the seam doc stays the single work-order. | `docs/PAPERCLIP-ING-INTEGRATION.md` and `scripts/check-paperclip-integration.sh` if the check asserts the list | The new rows are numbered and each names the lane that owns it; the existing check still passes; no other row is renumbered | `bash scripts/check-paperclip-integration.sh` and `bash scripts/check-docs.sh` | RC-6, RC-1 |
| **RC-8** | **Gate wiring and the docs index** | Wire the new checks into the gate of record and register the new doc/ADR: `scripts/verify.sh` `checks=(...)` entries, `Makefile` targets, `scripts/pytest-suites.txt` entries for the new suites, `docs/README.md` index rows. **One writer for all four build/index files.** | `Makefile`, `scripts/verify.sh`, `scripts/pytest-suites.txt`, `docs/README.md` | `make verify` names every new check; `check_count` rises by exactly the number of checks added; there are **no duplicate** check names (`grep -oE "^  '[a-z0-9-]+\|" scripts/verify.sh \| sort \| uniq -d` is empty); every new pytest suite is declared so `check-drift.sh` does not WARN | `make verify` — quoting `.verify/attestation.json`'s `result`, `exit_code`, `check_count`, `duplicates` and `git_sha` against the committed head | RC-2, RC-3, RC-4, RC-5, RC-6 |

### 8.3 Why this order, and what each edge buys

- **RC-1 first, because the transport choice is irreversible-ish.** Embedding a web
  console would violate ADR-0022's no-second-dashboard refusal; an inbound webhook from
  upstream would require an upstream endpoint that NG4 forbids us to add; a peer-hosted
  plane would create a second authority. Deciding this on paper is cheap; discovering it
  after RC-3 is not.
- **RC-2 before RC-3, because a closed vocabulary is what makes a control channel safe.**
  Without it, "add a verb" is an implicit API change, and the plan's "no silent action"
  rail has nothing to hang on.
- **RC-3 before RC-4, because refusals are only meaningful once there is a caller.** RC-4
  then adds the two properties upstream is strictest about and we currently lack:
  exactly-once effects and a *real* refusal when the plane is down.
- **RC-4 before RC-5, because the CLI must be able to trust a receipt.** A CLI that prints
  a receipt it cannot rely on is worse than no CLI.
- **RC-6 can start as soon as RC-2 lands** — it is a mapping over a frozen vocabulary and
  does not need the channel to exist. This is the parallel branch; it keeps a second lane
  busy without touching RC-3/4/5's files.
- **RC-8 last and alone, because the four files it owns are shared build files.** Every
  other child's gate is *delivered and run directly*; only RC-8 makes them reachable from
  `make verify`, exactly as the #420 and #499 precedents did.

### 8.4 The safety rails every child inherits

1. **Default-OFF.** `surfaces.remote_control` and the CLI's upstream flag ship OFF in
   `infra/feature-flags/registry.yaml`, checked **before** AuthN — an unpromoted surface
   is invisible, not merely unauthorised (the pattern `docs/LIVE-DATA-BRIDGE.md` already
   uses).
2. **One audit record per control action.** Appended to the existing ledger rail and the
   existing slug stream; **no new ledger** (ADR-0012's one-authority rule).
3. **No silent action.** Every failure mode is a **named refusal**: `401` unknown caller,
   `403` insufficient capability, `409` duplicate in-flight command, `422` verb outside
   the vocabulary, `503` plane unreachable. A command that cannot be delivered is never
   reported as delivered.
4. **Irreversibility is declared, not inferred.** A verb whose `effect_class` is
   `irreversible` requires an explicit confirmation token in the request; the vocabulary,
   not a code path, is what declares it.
5. **Monitoring verbs file tickets.** A threshold crossed produces a **ticket**, never a
   control action (ADR-0022 refusal 4).
6. **The CLI is not a dashboard.** Text and JSON only; no frame, no filter bar, no
   timeline.
7. **Peer changes go to the peer's board.** Any upstream gap RC-6 discovers is a
   **direction issue on `paperclipai/paperclip`**, recorded here and filed there — never
   an edit from this repo (NG4).

## 9. Evidence index

Ours (opened in this lane's worktree at `origin/master` `ffb2688`):

- Control verbs: `fleet/control.py` (18-verb registry in `build_parser()`; `_loop_pid()`, `_send_control()`, `os.kill`, `_tmux`), `fleet/channel.py` (mailbox constants, `cmd_order` refusal, verb registry), `fleet/console.py`, `fleet/watchdog.py`, `fleet/monitor.py`, `fleet/cron.py`, `fleet/health.py`, `fleet/health_publish.py`, `fleet/terminal.py`, `fleet/brain.py`, `fleet/CONTRACT.md` §0.
- Governance verbs: `governance/dispatch/cli.py`, `governance/reconcile/cli.py`, `governance/lifecycle/cli.py`, `governance/knowledge/cli.py`, `governance/board/cli.py`.
- HTTP surface: `portal/server/httpd.py` (`host="127.0.0.1", port=8787`), `portal/server/app.py` (the GET-only refusals), `portal/server/fleet.py`, `portal/server/bridge.py`, `portal/server/controls.py`.
- Seam: `integrations/paperclip/api/surface.py`, `integrations/paperclip/client.py`, `integrations/paperclip/cli.py`, `integrations/paperclip/auth/`.
- Decisions and gates: `docs/decision-records/ADR-0011`, `ADR-0012`, `ADR-0013`, `ADR-0014`, `ADR-0015`, `ADR-0016`, `ADR-0022`; `docs/PAPERCLIP-ING-INTEGRATION.md`, `docs/PAPERCLIP-ING-GAP-ANALYSIS.md`, `docs/FLEET-DASHBOARD-GAP-ANALYSIS.md`, `docs/LIVE-DATA-BRIDGE.md`, `docs/CROSS-REPO-EXECUTION-BOUNDARY.md`, `docs/OBSERVABILITY.md`, `docs/EXECUTION-PLAN.md`; `scripts/verify.sh`, `scripts/check-docs.sh`, `scripts/check-paperclip-canonical-module.sh`.

Upstream (read-only via `gh api repos/paperclipai/paperclip/contents/<path>`):
`server/src/routes/agents.ts`, `server/src/routes/approvals.ts`, `server/src/routes/activity.ts`,
`server/src/routes/dashboard.ts`, `server/src/routes/goals.ts`, `server/src/routes/issue-tree-control.ts`,
`server/src/routes/decisions.ts`, `server/src/routes/decision-queues.ts`, `server/src/routes/org-chart-svg.ts`,
`server/src/routes/health.ts`, `server/src/modules/{active-run-watchdog,run-dispatch,wake-queue}/**`,
`cli/src/commands/client/agent.ts` and `cli/src/__tests__/agent-lifecycle.test.ts`,
`packages/paperclip-runner/SEMANTIC_ACTIONS.md`, `docs/cli/control-plane-commands.md`,
`docs/api/{overview,agents,approvals,activity,costs,goals-and-projects,issues,routines}.md`,
`DESIGN.md`, `ROADMAP.md`.

## See also

- [`FLEET-DASHBOARD-GAP-ANALYSIS.md`](FLEET-DASHBOARD-GAP-ANALYSIS.md) — what a terminal cannot show (the read half).
- [`PAPERCLIP-ING-GAP-ANALYSIS.md`](PAPERCLIP-ING-GAP-ANALYSIS.md) — capability families upstream vs ours (the feature half).
- [`PAPERCLIP-ING-INTEGRATION.md`](PAPERCLIP-ING-INTEGRATION.md) — the frozen seam and its mismatch work-order.
- [`LIVE-DATA-BRIDGE.md`](LIVE-DATA-BRIDGE.md) — the existing versioned read transport and its flag-gate pattern.
- [`CROSS-REPO-EXECUTION-BOUNDARY.md`](CROSS-REPO-EXECUTION-BOUNDARY.md) — NG4, the rule the proposal obeys.
- [`EXECUTION-PLAN.md`](EXECUTION-PLAN.md) — lane ownership for the children above.
- [`decision-records/ADR-0013-paperclip-ing-integration.md`](decision-records/ADR-0013-paperclip-ing-integration.md) — adopt-over-embed-or-fork, and what stays ours.
- [`decision-records/ADR-0022-telemetry-exposition-authority-split.md`](decision-records/ADR-0022-telemetry-exposition-authority-split.md) — no second dashboard; signal → ticket → human.
