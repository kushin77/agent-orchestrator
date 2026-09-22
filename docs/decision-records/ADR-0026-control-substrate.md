---
id: ADR-0026
status: accepted
date: 2026-09-14
deciders: [owner]
req: []
supersedes: []
---

# ADR-0026: The control substrate — the supervision and live-view mechanism that replaces tmux

## Status

`accepted` — ratified on the PR for issue #563, child of EPIC #551 ("Remote
control command center CLI — a reachable, identified, audited control channel
for the fleet").

**It supersedes no ADR.** No record ever made tmux the control plane, so there
is nothing to flip to `superseded`: tmux became the control plane by accretion
(`fleet/control.py` grew `live`/`attach`; `fleet/console.py` grew a tmux-shaped
frame). This record retires a *practice*, not a decision. That is why the
refusal has to be written down at all — an unrecorded default is invisible, and
this one is measured in two files.

**It consumes [`ADR-0025`](ADR-0025-remote-control-transport.md) and re-decides
none of it.** ADR-0025 froze the *channel*: the route family lives in the
existing console app, flag-gated OFF and checked before AuthN; the caller is the
existing console session (`os-session-token` via `_require_session`); the verb is
authorised by the existing `identity/rbac` two gates, rechecked per call; an
applied command writes exactly one audit record on the **existing** rails
(`telemetry/ledger/` + `.fleet/slog.jsonl`); monitoring verbs file tickets and
only operator-requested verbs write fleet state. All of that stands verbatim and
is the foundation here. ADR-0025 decided **who may command and what is
recorded**. It deliberately did **not** decide **what actually executes the
command** — and that gap is this record.

### Numbering note (issue #563 named no number; re-derived at lane start)

Measured on this lane's base, `origin/master` `3d60d62`, with the issue's own
method — the test is not "is the number free as a *file*" but "is it already
*referenced*", because an unprefixed citation in merged code already reads as
this repo's own:

1. **Files.** `docs/decision-records/` carries `ADR-0001`–`ADR-0018`, `ADR-0022`,
   `ADR-0023` and `ADR-0025`. `ADR-0019`–`ADR-0021` have no file by the index's
   own rule (they are CMR-hub records, cited with a prefix).
2. **Citations.** `git grep -hoE "ADR-00[0-9][0-9]" origin/master | sort -u`
   returns exactly `0001`–`0025`, plus `0029`, `0030`, `0037` and `0042`. So
   `0026`, `0027` and `0028` have **no file and no citation anywhere in the
   tracked tree**; the lowest free number is **`0026`**.
3. **Claims.** No open issue or PR names `ADR-0026` (`AGENTS.md` rule 20's chain
   discipline; the board's `ADR-00NN` claims are `0022` #495, `0023` #501,
   `0030` #358/#329, `0025` #552 — all landed).

**Therefore this record lands as `ADR-0026`.** Taking `0024` (free as a file,
cited unprefixed in `portal/server/surfaces.py`) is exactly the error the
[index's numbering rule](README.md) forbids and which produced ADR-0025 over
ADR-0024; `0026` has neither a file nor a citation, so nothing is re-pointed.

## Context

### The gap ADR-0025 left open, and why it is not small

ADR-0025 replaced an *implicit* front door with a declared one. It did not touch
what sits behind the door. Measured today, behind the door is a **local PID and a
host-local multiplexer**:

| Fact | Evidence |
|---|---|
| `pause` / `resume` / `stop` / `halt` are delivered as a **flag file** in `.fleet/` read by the loop | [`../../fleet/control.py`](../../fleet/control.py) — `cmd_pause`/`cmd_resume`/`cmd_stop`/`cmd_halt` all call `_send_control(...)`; `cmd_status` prints the `paused`/`stopping` flags |
| `kill` and `restart` are delivered by **`os.kill`** | [`../../fleet/control.py`](../../fleet/control.py) — `_signal_loop` calls `os.kill(pid, signum)`, and `cmd_restart` polls liveness with `os.kill(pid, 0)` |
| The PID is read from a **host-local heartbeat file** | `_loop_pid()` reads `.fleet/sister.heartbeat.json` and returns `int(beat["pid"])` |
| The operator's way in is **tmux**, and the console is a tmux pane | `fleet/control.py` builds a five-window session (`dashboard`/`brain`/`sister`/`monitor`/`events`) and `attach` runs `tmux attach`; [`../../fleet/console.py`](../../fleet/console.py) renders into a "fixed 96-column frame" because "the dashboard's home is a tmux pane" |
| The TUI is **host-bound by construction** | `fleet/console.py` imports `channel` and `runtime` by bare name and reads `ROOT / ".fleet"` — it cannot run on a machine that is not the fleet host |

`fleet/control.py`'s own docstring already concedes the shape: the tmux session
is "a VIEW, never the host". The problem is that the *control* path is the same
local reach, and the loop is a **local PID**, so "control" means "a process on
the box we can signal".

### The measured runtime this decision must fit

| Surface | Measured fact (source) |
|---|---|
| The fleet's services | [`control-plane-service/main.tf:30`](../../infra/terraform/modules/control-plane-service/main.tf) and [`web-surface/main.tf:45`](../../infra/terraform/modules/web-surface/main.tf) are both `resource "google_cloud_run_v2_service" "this"` |
| Ingress, control-plane | `ingress = var.ingress`, default `INGRESS_TRAFFIC_INTERNAL_ONLY` — [`control-plane-service/variables.tf`](../../infra/terraform/modules/control-plane-service/variables.tf) |
| Ingress, web | `ingress = "INGRESS_TRAFFIC_ALL"` **deliberately**, plus a `google_cloud_run_v2_service_iam_member.public` granting `roles/run.invoker` to `allUsers` — this is the one surface meant to be public |
| Scaling / traffic primitives | **Not declared anywhere.** `grep -rn "min_instance_count\|max_instance_count\|TRAFFIC_TARGET_ALLOCATION\|scaling {"` over the tracked tree returns **nothing** |
| Everything is OFF | every module is `count = var.enabled ? 1 : 0`; `infra/terraform/variables.tf` carries ten `enable_*` flags all defaulting `false`; [`registry.yaml`](../../infra/feature-flags/registry.yaml) sets `default_policy: off` |
| Durable engine | [`../../engine/core/README.md`](../../engine/core/README.md) — "Temporal-shaped but Temporal-free"; durable, per-tenant namespaced, sagas, retries, `resume` by replaying the append-only transcript |
| The Temporal seam | [`../../engine/core/temporal.py`](../../engine/core/temporal.py) — the `TemporalAdapter` protocol plus the normative engine↔Temporal mapping table, "intentionally **not** wired" |
| The live view we already have | [`../../portal/server/fleet.py`](../../portal/server/fleet.py) — an SSE stream of rung snapshots (`SSE_EVENT = "snapshot"`), surface `fleet_projection`; [`../../portal/server/live_feed.py`](../../portal/server/live_feed.py) — an SSE stream of per-dispatch call records and guardrail verdicts, surface `telemetry_live_feed`. Both flag-gated OFF, both checked before AuthN |
| The client stack | [`../QA-GATE.md`](../QA-GATE.md) — "shell + Python stdlib + PyYAML + pytest. No network, no containers"; the repo carries **no dependency manifest at all** (`requirements*.txt`, `pyproject.toml`, `setup.py` — none exist) |
| The verb vocabulary | [`../../control-plane/control/verbs.yaml`](../../control-plane/control/verbs.yaml) — 49 verbs, `fleet.control.*` exposed with `capability: fleet:operate` |

### The question RC-2 already forwarded to this lane

The vocabulary this ADR must serve is already declared, and it names this record
as the owner of the answer. [`verbs.yaml`](../../control-plane/control/verbs.yaml)
declares `fleet.kill` with `immediate: true` and the note:

> Immediate termination (no graceful drain). Distinct from fleet.stop by
> `immediate: true`. RC-9 must name the substrate primitive that replaces
> SIGKILL -- os.kill is refused as the control mechanism.

That is a **pre-registered acceptance criterion from a sibling lane**, and D1
below answers it by name.

### Decision space

| Question | Options | Chosen |
|---|---|---|
| **Where a rung's lifecycle state lives** | in the process (a resident loop + a flag file) · in a Cloud Run Service revision · **in the durable engine's workflow transcript** | **the durable engine** (D1) |
| **The actuator for a lifecycle verb** | `os.kill` on a heartbeat PID · a `.fleet/` flag file · **the Cloud Run v2 control plane plus the engine's own workflow operations** | **Cloud Run v2 + engine operations** (D1) |
| **Durable execution of record** | `engine/core` (ours) · the Temporal transport (`temporal.py`, seamed and unwired) | **`engine/core`**, Temporal behind a stated promotion trigger (D2) |
| **The live view** | a host-local multiplexer (`tmux attach` + `tail -f`) · **the existing authenticated SSE streams, consumed by a CLI `--follow`** · a new browser terminal | **the existing SSE streams** (D3); no web terminal (D3.3) |
| **The client** | extend the tmux TUI · a local-only Rich/Textual TUI · a bespoke web dashboard · a systems-language client | **a Python-stdlib, server-agnostic terminal client** (D5) |
| **What tmux becomes** | the control plane · a view · **a developer convenience and local debug multiplexer** | **convenience only** (D4) |

## Decision

### D1 — Supervision: the durable engine owns lifecycle; Cloud Run v2 is the actuator

**A rung's lifecycle is a durable engine workflow state, and every lifecycle verb
is executed by the engine plus the Cloud Run v2 control plane. No lifecycle verb
is delivered by a signal to a local process. `os.kill` is refused as the control
mechanism — and, under this substrate, it is not merely refused but
*unavailable*, because there is no stable PID to signal.**

**D1.1 — Why `os.kill` is structurally unavailable, not merely forbidden.** A
Cloud Run v2 service is declared with no host, no pod and no persistent session
(two `google_cloud_run_v2_service` resources, above), and it scales to zero by
default because nothing in [`infra/terraform/**`](../../infra/terraform/modules/control-plane-service/main.tf)
declares a minimum instance count. An instance that may not exist cannot be
addressed by PID: the heartbeat file `_loop_pid()` reads is a **host-local
artifact**, and there is no host. Retiring `os.kill` therefore costs nothing
operationally — the handle it needs stops existing. The refusal is a
consequence of the substrate, which is the strongest form this rule can take.

**D1.2 — The substrate shape follows what `kill` must mean.** The two Cloud Run
v2 shapes have different termination semantics, and that difference is decisive:

- a **Service** is the unit of *residency*: it is a set of immutable **revisions**
  plus a **traffic** allocation, and its instance count is governed by
  `template.scaling.min_instance_count`. It has **no hard-stop primitive** — the
  platform drains in-flight work up to the request timeout.
- a **Job** is the unit of *execution*: each run is an **Execution**, and
  `executions.cancel` is the platform's own SIGTERM-then-SIGKILL, issued by the
  PID namespace's owner.

So: **a rung that must be resident is a Service; a rung that must be hard-stoppable
is a Job.** Nothing in this decision requires a rung to be reshaped today — it
requires the *verb's semantics* to be honest about which one it is addressing.

**D1.3 — The mapping, one primitive per verb.** Each cell names a real Cloud Run
v2 primitive (or the engine operation that replaces it), and the `fleet.*` verb
it serves.

| Verb | Engine primitive (the durable fact) | Cloud Run v2 primitive (the actuator) | What "the effect" is |
|---|---|---|---|
| **`pause`** (`fleet.pause`) | the rung's workflow **parks at a durable signal-waiting step**; its state is in the append-only `EventRecord` transcript, not in a process | `template.scaling.min_instance_count = 0` on the rung's revision — **scale to zero** | The rung stops taking new work and stops costing an instance. Safe *only because* the state is durable: the container is disposable, the run is not. |
| **`resume`** (`fleet.resume`) | `signal_workflow(namespace_id, workflow_id, "resume")` — the engine **replays the transcript and continues mid-flight** (`WorkflowExecution.resume`) | `template.scaling.min_instance_count = 1` plus a `traffic` block at **100% on the rung's revision** | The rung serves again from exactly where it parked; no replay of completed work, no lost step. |
| **`stop`** (`fleet.stop`) | the workflow transitions to its **terminal** `stopped` state — no further steps are dispatched, and the in-flight step is allowed to complete | the `traffic` block moves **off the rung's revision** (0%). The revision is **immutable and retained**, so `start` is a traffic change, never a redeploy | Graceful drain. Reversible by an explicit `start`, which is what `effect_class: stop` promises in the vocabulary. |
| **`kill`** (`fleet.kill`) | `terminate_workflow(namespace_id, workflow_id, reason)` — terminal, and deliberately **no compensation run** | for a job-shaped rung: **`jobs.executions.cancel`** (the platform's SIGTERM→SIGKILL). For a resident service: `min_instance_count = 0` + traffic 0%, and the platform reaps the instance | Hard stop, executed by the **platform that owns the PID namespace** — not by us. `immediate: true` in the vocabulary is satisfied by the platform's cancel, never by `os.kill`. |

Two further verbs inherit the same substrate rather than needing a fourth
primitive: **`fleet.restart`** is a **new revision + traffic 100%** (Cloud Run's
native cutover), and **`fleet.halt`** is `fleet.stop` at fleet scope — both are
stated here so a later lane does not re-derive them.

**D1.4 — Canary and rollback are native, and that is a second reason to accept
this substrate.** Because revisions are immutable and traffic is a first-class
allocation, a bad rung version is one traffic change from being undone, and a
partial rollout is a percentage rather than a code path. We do not build either;
the substrate already has them — which is precisely why the control plane should
not grow its own.

**D1.5 — What we must still declare.** The primitives above are **available but
undeclared**: the tracked tree contains no `scaling` block and no `traffic` block
anywhere. Delivering this decision therefore requires a later **IaC lane** to
declare `template.scaling` and `traffic` on the control-plane module — flag-gated
OFF like everything else (AO-GR-6), never applied ad hoc (GR-5). This record
fixes the *mapping*; it does not author the Terraform.

> **Amended 2026-09-22:** every flag-gated-OFF / "Nothing ON by default"
> reference in this record (here and at the "Both are flag-gated OFF" and
> "Nothing ON by default" passages below) was reversed by
> policy-gr5-enabled-by-default (2026-09-21, AO-GR-6); see
> docs/GOLDEN-RULES.md#ao-gr-6--flag-gated-off-by-default.

### D2 — Durable execution: our engine core is the substrate of record; Temporal stays seamed

**The run state machine is ours — `engine/core` — and the Temporal transport
remains a frozen contract that is deliberately unwired. It is promoted only on a
stated trigger, and promotion is itself a new ADR.**

**D2.1 — Why ours, today.** `engine/core` already has every property this
decision needs: the transcript is durable and append-only
([`../../engine/core/README.md`](../../engine/core/README.md)), a workflow
resumes mid-flight by replaying it, namespaces are per-tenant, sagas compensate in
reverse, retries are per-step, and it runs **offline with no server and no
network** — which is what makes the whole thing testable in the gate of record.
Choosing Temporal now would trade that determinism for a server we would have to
run, secure, and pay for, in exchange for nothing this workload currently needs.

**D2.2 — Why the seam already exists and is left alone.**
[`../../engine/core/temporal.py`](../../engine/core/temporal.py) is not a stub: it
fixes the `TemporalAdapter` protocol (`start_workflow` / `get_status` /
`signal_workflow` / `terminate_workflow`, namespace- and workflow-scoped) and the
**normative mapping table** from every engine concept onto Temporal's
(namespace-per-tenant, task queues, Workflow/Activity, history, saga, retry
policy, replay). It is deliberately duck-typed so no SDK enters this repo. That
is the correct shape for a seam: the *contract* is frozen so the *wiring* can
change without touching the core. `D1.3`'s verbs are expressed in the engine's own
operations, which are exactly the operations this protocol mirrors — so the
promotion is a wiring change, not a redesign.

**D2.3 — The promotion trigger, stated.** Promote the Temporal transport when
**any one** of these is true:

1. **Horizontal execution.** The engine must run more than one instance
   (`max_instance_count > 1` in the control-plane module) **while a single run's
   steps must be distributed across those instances**. `FileJsonlEventStore` is a
   local file; it stops being a shared substrate the moment two instances own one
   run's history.
2. **Cross-host run continuity.** A run must survive the loss of the **entire
   Cloud Run service** — a region failover, or a delete-and-recreate — and resume
   under the same workflow id. Surviving a *process* restart is already handled by
   replay; surviving the *host* is not.
3. **Externally hosted history required.** A contract or an auditor requires the
   workflow history to live in an independently hosted, independently auditable
   store rather than in our own event log.

Whichever fires, promotion changes the **substrate of record**, so it is a **new
ADR** (ADR-0010's one-canonical-home rule), not an import. The trigger is
observable — it is a `max_instance_count` value or a stated continuity
requirement — so this is a decision someone can be held to, not a preference.

### D3 — The live view: the existing authenticated SSE streams, consumed by a CLI `--follow`

**"Attach and watch" survives as a *client* of an authenticated stream, never as a
local multiplexer. The live view is the existing SSE surfaces, consumed by a CLI
`--follow`. No web terminal ships.**

**D3.1 — The streams already exist and are already authenticated.** Two, and the
difference matters:

- [`../../portal/server/fleet.py`](../../portal/server/fleet.py) — the **fleet
  projection** stream: rung snapshots, `SSE_EVENT = "snapshot"`, surface
  `fleet_projection`. This is the rung state a `--follow` needs.
- [`../../portal/server/live_feed.py`](../../portal/server/live_feed.py) — the
  **live telemetry** feed: per-dispatch call records and guardrail verdicts,
  surface `telemetry_live_feed`. This is what the fleet is *doing*.

The brief named only `live_feed.py`; the rung-state stream is the other half, and
a cockpit needs both. Both are flag-gated OFF (`surfaces:` in
[`registry.yaml`](../../infra/feature-flags/registry.yaml)) and both are checked
**before** AuthN, so an unpromoted stream is invisible rather than merely refused —
the [`../LIVE-DATA-BRIDGE.md`](../LIVE-DATA-BRIDGE.md) precedent ADR-0025 D1.1
adopted. Neither opens a new data plane: they tail stores that already exist and
they were built to make "an empty feed is not 'no traffic'" explicit.

**D3.2 — `--follow` replaces `tmux attach` + `tail -f`, and inherits ADR-0025
verbatim.** The CLI's follow mode is a client of D3.1's streams over the same
identity path ADR-0025 D2 fixed: the console session cookie, verified by
`_require_session`, with RBAC evaluated per call. It carries **no credential of
its own**, opens **no second listener**, and writes **no fleet state** — a
watcher is by construction a reader, so a follow can never become an unaudited
write path.

**D3.3 — No web terminal ships. Declined, not deferred-with-intent.** The issue
asked whether one ships, and the answer is no:

- It would be a second human surface, which
  [`ADR-0022`](ADR-0022-telemetry-exposition-authority-split.md)'s
  no-second-dashboard refusal forbids, and `D6` restates.
- A terminal in a browser is a **shell-shaped** surface: its natural next step is
  an interactive command channel, i.e. exactly the un-audited path D4 forbids.
- It is not needed. D3.1's streams plus D5's client give the operator the same
  view from a machine that is not the fleet host — which was the whole defect.

**If a web terminal is ever proposed, the burden is on the proposal**, and it must
clear three bars at once: it authenticates through ADR-0025's identity path
verbatim, it writes nothing that does not go through the audited control API, and
it is a **client** of D3.1's streams rather than a new source of truth. Those bars
are stated now so the answer is a rule rather than a re-argument.

### D4 — What tmux becomes

**tmux is a developer convenience and a local debug multiplexer. It is not the
control mechanism, not the source of truth, and never an un-audited path that can
write fleet state.**

Stated as four refusals, because each is a way the old habit could return:

1. **Not the control mechanism.** No lifecycle verb may be delivered by
   `tmux attach`, by keystrokes in a pane, or by any path whose only actor is a
   human at a terminal. Every lifecycle verb is D1's substrate path.
2. **Not the source of truth.** `fleet/control.py` already says the session is "a
   VIEW, never the host"; this record upgrades that from a comment to a rule. The
   truth is the durable transcript (D2) plus the served projections (D3).
3. **Never an un-audited write path.** A pane that can type into the fleet is a
   control path with no caller identity and no record — precisely the gap ADR-0025
   exists to close.
4. **Still legitimate, locally.** A developer may run `tmux` on their own machine
   to watch logs, split panes, or debug. Nothing in this record removes that. What
   is removed is any suggestion that the *product's* control plane is a
   multiplexer.

### D5 — Client architecture: a client of the served API, in the sanctioned stack

**The terminal cockpit is a client of the same served API a browser would use. The
API is the product; the terminal is one client of it. It must run from a machine
that is not the fleet host.**

**D5.1 — The named client: Python, stdlib only, no TUI framework.** The cockpit is
a **Python 3 client that speaks the RC-3 control API and consumes D3.1's SSE
streams**, rendering with the same alternate-screen technique
[`../../fleet/console.py`](../../fleet/console.py) already proves in-tree (an ANSI
palette plus the alternate screen, `\033[?1049h`). It is **a new client module —
not `fleet/console.py` extended in place**, because that file is host-bound (D5.2).

The framework is deliberately **"none"**, and that is a *decision* justified by
measurement rather than a shrug: [`../QA-GATE.md`](../QA-GATE.md) fixes the stack
as "shell + Python stdlib + PyYAML + pytest", and the repository carries **no
dependency manifest at all**. Naming a TUI framework would therefore be a
**supply-chain decision** smuggled in as a rendering choice. If Textual (or any
equivalent) is wanted later, that is a **new ADR** about dependencies — not an
import line in a client lane.

**D5.2 — The alternatives, rejected with the reason each was rejected.**

| Alternative | Rejected because |
|---|---|
| **Extend the current tmux TUI** (`fleet/console.py`) | It is **structurally host-bound**: it imports `channel` and `runtime` by bare name and reads `ROOT / ".fleet"`. It cannot run on another machine — the exact requirement. Extending it in place would also re-couple the cockpit to tmux, which D4 retires. |
| **A local-only Rich/Textual TUI** | Rejected on the **data path**, not the renderer: "local-only" means it reads host files, which is the same defect as the row above. It also carries the dependency issue in D5.1 (no manifest; the stack is enumerated). |
| **A bespoke web dashboard** | Refused by [`ADR-0022`](ADR-0022-telemetry-exposition-authority-split.md)'s no-second-dashboard rule, and deferred again by D7. The web SPoG is the human surface and is sourced from paperclip.ing ([`../FLEET-DASHBOARD-GAP-ANALYSIS.md`](../FLEET-DASHBOARD-GAP-ANALYSIS.md)); a second one is the failure that EPIC exists to prevent. |
| **A systems-language client (Go/Rust)** | It buys nothing this workload needs and costs a second toolchain, a second build pipeline and a second dependency-audit surface. The server is Python and the client is a thin HTTP+SSE consumer; the performance argument does not exist at this layer. |

**D5.3 — What the client may never do.** It **reuses ADR-0025's identity/audit
path verbatim**: it presents the console session, it is authorised by
`identity/rbac` per call, and every action it takes goes through the control API
where the audit record is written. The cockpit **may never hold its own
credentials** (no second token, no service account, no stored secret) and **may
never write fleet state directly** (no `.fleet/` write, no claim, no branch, no
worktree). If the client cannot name the principal on whose behalf it is acting,
it cannot act.

### D6 — A client of one API, not a second dashboard

**A terminal cockpit is a client of ONE API, not a second dashboard. It consumes
the same served projection the web SPoG consumes. There is no second data plane,
no second ledger and no re-derived human surface.**

This is the ruling that keeps [`ADR-0022`](ADR-0022-telemetry-exposition-authority-split.md)
intact, and it is what makes adding a second client later a *client* choice rather
than an architecture change. Concretely:

- **One API.** The cockpit's actions are RC-3's control API; its reads are D3.1's
  served projections. It does not read `.fleet/` directly, and it does not
  re-derive a figure the API already serves.
- **One ledger.** The audit record is written **once**, on the existing rails, by
  the control API (ADR-0025 D3). The cockpit reports the receipt it is given; it
  never authors one.
- **No re-derived surface.** A number that appears in the cockpit is a number the
  API sent. Where the API says `NO_DATA`, the cockpit says `NO_DATA` (D10).

### D7 — Terminal-first, browser-deferred

**Ship the terminal client first. A browser client is explicitly deferred, and
when it lands it must be a second client of the same API — never a parallel
surface. A web terminal is not approved by this decision.**

Deferred ≠ forbidden for a *general* browser client; it is a sequencing rule:
there is exactly one API, and it is built for the terminal first so the terminal
drives its shape. The web terminal specifically is declined (D3.3). The
consequence for later lanes is stated so they cannot drift: any browser work
arrives as D5.3's client — same identity path, same API, same receipts.

### D8 — Role tiers are views, not products

**CTO / VP-Eng / Manager / Analyst are named workspaces over ONE function set,
scoped by the existing [`identity/rbac`](../../identity/rbac/guard.py) plus
`identity/entitlements/`. Never four builds. Never four permission systems.**

- **One function set.** Every panel, command and view is declared once (D9). The
  role workspaces are *arrangements* of that one set, not subsets of a product.
- **Scoping is the existing engine, not a new one.** A role's reach is resolved
  by `identity/rbac`'s two gates (scope, then permission) and the entitlements
  engine — rechecked per call (ADR-0025 D2.4). The cockpit adds no second
  permission vocabulary and no role table of its own.
- **A role can only narrow.** The hard floor, stated so it is testable: **a role
  can never make visible something the API would not already serve that caller.**
  A named workspace is a *lens*, and a lens cannot widen a scope — if a view of
  CTO data appears for an Analyst, the defect is in the API's authorisation, not
  in the view.

### D9 — Extensibility: a declaration, not a fork

**Adding a domain — FinOps, SLO, audit, tickets, paperclip — must be a
declaration in a closed function registry, not a fork of the client.** That
registry is scoped as **RC-10 (issue #565)** and must be decided **before** the
cockpit is built as **RC-11 (issue #566)**.

This is a dependency edge, not a preference, and it is the direct analogue of the
verb vocabulary ADR-0025 D2.3 made `fleet`'s capability source: **one closed
declaration that the client renders and the server enforces**, so a new domain
cannot arrive as a bespoke panel with its own permission story. The registry names
a function (what it does, which capability it needs, which projection it reads);
the client renders whatever is declared.

**The ordering is the point.** If the cockpit is built first, every domain
becomes a fork of it and the "one function set" of D8 dissolves. So: **#565
(RC-10) before #566 (RC-11)**, and this record is the authority for that edge.

### D10 — Honesty in the client

**The cockpit carries the repository's existing honesty rules, explicitly.** Each
is stated as a testable obligation rather than a virtue:

1. **A panel that cannot load says so.** A failed read renders as a failure with
   its reason — never as a silently empty panel. An empty panel reads as "all
   quiet", which is a claim the client is not entitled to make.
2. **`NO_DATA` is never rendered as OK.** Where a source reports absence of data,
   the client renders absence. It may not fall back to a green state, and it may
   not substitute zero.
3. **An unpromoted surface is invisible, and the flag is named.** If a stream or
   family is OFF, the cockpit shows it as absent **and names the flag**
   (`surfaces.fleet_projection`, `surfaces.telemetry_live_feed`, … — read from
   [`registry.yaml`](../../infra/feature-flags/registry.yaml)), so an operator can
   tell "off" from "broken". This is the client-side face of ADR-0025 D1.1's
   flag-before-AuthN rule.
4. **Every action prints a receipt or an explicit refusal.** An action either
   shows the audit record / identifier the API returned, or it shows the refusal
   — including the 401/403/409/503 shapes ADR-0025 D3 fixed. There is no silent
   success and no optimistic UI: a client that renders "OK" before the API
   answered is claiming evidence it does not have.

D3.1's streams already implement this discipline server-side — an absent store is
`NO_STORE`, not "no traffic" — and D10 is the rule that the client must not undo
it while rendering.

### D11 — Relationship to the existing decisions

This record **consumes** the following and re-opens none of them:

| Decision | How this record relates | What it does not re-open |
|---|---|---|
| [`ADR-0010`](ADR-0010-canonical-copy-ownership.md) | one canonical home — the reason D2.3's Temporal promotion is a *new ADR* | the canonical-copy rule |
| [`ADR-0011`](ADR-0011-session-fleet-transport.md) | untouched; D2 changes the *durable execution* substrate, not the session-fleet mailbox | the file-mailbox transport and its operator-sender refusal |
| [`ADR-0012`](ADR-0012-hermes-paperclip-boundary.md) | D6's one-ledger rule; *map the policy, do not couple the runtime* | the Hermes/Paperclip ownership boundary |
| [`ADR-0013`](ADR-0013-paperclip-ing-integration.md) | the adopt-over-embed mode D5.2's web-dashboard rejection relies on | the integration mode |
| [`ADR-0014`](ADR-0014-ticket-single-join-node-contract-v2.md) | a signal still drives a ticket; D1's control verbs are still operator-requested | the ticket contract v2 |
| [`ADR-0015`](ADR-0015-routing-seam-single-authority.md) | untouched | routing's single authority |
| [`ADR-0016`](ADR-0016-paperclip-boundary-single-module.md) | untouched | the boundary module's home |
| [`ADR-0022`](ADR-0022-telemetry-exposition-authority-split.md) | D3.3, D5.2 and D6 consume the no-second-dashboard refusal; D6 consumes the exposition split | the machine/human surface split and the refusal |
| [`ADR-0023`](ADR-0023-conversational-surface.md) | D5's "client of one API" is the same client posture | the conversational surface |
| [`ADR-0025`](ADR-0025-remote-control-transport.md) | builds directly on it: D1 supplies the actuator ADR-0025 left open; D3/D5/D10 reuse its identity, audit and refusal shapes **verbatim** | the channel, the caller, the authority split, the refusals |

It likewise does **not** re-decide what a verb *does* (`fleet/control.py` keeps
its semantics and D1.3 changes only *how the effect is delivered*), the claim
ledger's authority, the FinOps tiers, or the boundary adapter's shape.

## The standing rules, with the consequence each forces

| Rule | Consequence this record fixes |
|---|---|
| **No second dashboard** ([`ADR-0022`](ADR-0022-telemetry-exposition-authority-split.md)) | D3.3 declines the web terminal; D5.2 refuses the bespoke dashboard; D6 fixes the client-not-dashboard ruling. The cockpit grows no frame, no filter bar, no timeline beyond what a terminal renders — it is text and structured output, and any new *view* obeys ADR-0022's split. |
| **No second ledger** ([`ADR-0012`](ADR-0012-hermes-paperclip-boundary.md)/[`ADR-0015`](ADR-0015-routing-seam-single-authority.md); ADR-0025 D3) | D6: one audit record, written once by the control API on the existing rails (`telemetry/ledger/` + `.fleet/slog.jsonl`). The cockpit **prints receipts, never authors them**. |
| **Nothing ON by default** (AO-GR-6, GR-5) | Every surface this decision consumes is already `default: off` in [`registry.yaml`](../../infra/feature-flags/registry.yaml), and D1.5 requires the new scaling/traffic declarations to ship flag-gated OFF too. The cockpit is a client of OFF surfaces until a reviewed go-live promotes them — so it ships **invisible**, not merely unauthorised. |
| **No peer repo edited** (NG4, [`../CROSS-REPO-EXECUTION-BOUNDARY.md`](../CROSS-REPO-EXECUTION-BOUNDARY.md)) | Everything here is this repo's files. The upstream control verbs ADR-0025 D1.2 permits as a *mapping* are unchanged; any upstream gap discovered while building the cockpit is a **direction issue on that repo's board**, filed and reported, never patched from here. |
| **A signal drives a ticket, never a silent action** (ADR-0022 refusal 4; ADR-0025 D3) | D1's verbs are operator-requested and audited; D3.2's `--follow` is a read-only client; D5.3 forbids the cockpit from holding credentials or writing fleet state. Nothing in this record lets a threshold, watchdog or export drive a lifecycle verb. |
| **Verify before done** (GR-12, [`../QA-GATE.md`](../QA-GATE.md)) | D1.1's and D1.5's claims are checkable: the absence of any scaling/traffic declaration is a grep, the client's stdlib-only requirement is a dependency audit, and D10's four obligations are negative-testable. A claim in a client that the API did not make is a defect this record names in advance. |

## Consequences

- **Positive.** The three-blocked-lane problem does not recur: RC-10 (#565) and
  RC-11 (#566) now have one frozen substrate to build on. Lifecycle stops meaning
  "signal a PID on the box" and starts meaning "change a durable workflow state
  and let the platform act", which is the only version of this that can work
  behind an authenticated API. `fleet.kill`'s pre-registered question has its
  named answer (`jobs.executions.cancel` / platform reaping). The live view
  becomes a client of streams that already exist and are already honest. And the
  refusal of `os.kill` stops being a rule someone must remember and becomes a
  property of the substrate.
- **Negative.** Two real costs are accepted. First, the primitives D1.3 depends
  on are **not yet declared** — this ADR is inert until an IaC lane lands
  `template.scaling` and `traffic` flag-gated OFF (D1.5), so the migration has a
  prerequisite in another lane. Second, **hard-kill has no primitive on a
  Service** (D1.2): a rung that must be immediately terminable has to be modelled
  as a Job, which is a deployment-shape constraint that may not fit every rung.
  Both are stated rather than papered over. A third, softer cost: the "no TUI
  framework" ruling (D5.1) means the cockpit is more work to build than importing
  a widget library would be — accepted deliberately, because it keeps a
  supply-chain decision out of a client lane.
- **Neutral.** Nothing ships on. `fleet/control.py` keeps all 18 verbs and their
  local semantics — D1.3 changes the *delivery* of the lifecycle effects, and its
  `os.kill` path becomes local developer tooling under D4. `fleet/console.py`
  keeps working where it works (on the host, in a pane) and is not deleted;
  it simply stops being anything a control path depends on. `engine/core` keeps
  its store; `temporal.py` keeps its frozen contract and stays unwired.
- **Follow-ups.** An **IaC lane** declares `template.scaling` and `traffic` on
  the control-plane module, flag-gated OFF (D1.5). **RC-10 / #565** decides the
  closed function registry, and must precede **RC-11 / #566**, which builds the
  terminal cockpit against RC-3's API and D3.1's streams (D9, D5). A lane
  re-points `fleet.kill` / `fleet.restart` / `fleet.halt`'s declared *lever* from
  `fleet/control.py`'s signal path to D1.3's substrate path in
  [`verbs.yaml`](../../control-plane/control/verbs.yaml). And if this decision is
  ever reversed, that is a **new** ADR — not an edit of this one.

## Evidence

Every claim above is drawn from committed content on this lane's base,
`origin/master` `3d60d62`:

- `infra/terraform/modules/control-plane-service/main.tf:30` and
  `infra/terraform/modules/web-surface/main.tf:45` — both
  `resource "google_cloud_run_v2_service" "this"`.
- `infra/terraform/modules/control-plane-service/variables.tf` — `ingress`
  defaults to `INGRESS_TRAFFIC_INTERNAL_ONLY`;
  `infra/terraform/modules/web-surface/main.tf:51,79` — `INGRESS_TRAFFIC_ALL` and
  the `allUsers` / `roles/run.invoker` member (public by design).
- `grep -rn "min_instance_count\|max_instance_count\|TRAFFIC_TARGET_ALLOCATION\|scaling {"`
  over the tracked tree (excluding `vendor/`) — **no matches**, i.e. the Cloud Run
  scaling and traffic primitives are available but undeclared.
- `fleet/control.py` — `_loop_pid()` (the `.fleet/sister.heartbeat.json` read),
  `_signal_loop` (`os.kill(pid, signum)`), `cmd_restart` (`os.kill(pid, 0)`), the
  five-window tmux argv, and `tmux attach`.
- `fleet/console.py` — the ANSI palette, the alternate-screen write, and the
  "96-column frame" whose stated reason is that the dashboard lives in a tmux
  pane.
- `control-plane/control/verbs.yaml` — the `fleet.kill` note naming RC-9 as the
  owner of the SIGKILL replacement; the `fleet.*` verbs with
  `capability: fleet:operate`.
- `engine/core/README.md` — the durable, resumable, per-tenant-namespaced engine;
  `engine/core/temporal.py` — the `TemporalAdapter` protocol and the normative
  engine↔Temporal mapping table, deliberately unwired.
- `portal/server/fleet.py` — the `fleet_projection` SSE stream;
  `portal/server/live_feed.py` — the `telemetry_live_feed` SSE stream;
  `portal/server/app.py` — `StreamResponse` and the pre-AuthN telemetry flag gate.
- `identity/rbac/guard.py` — the scope-then-permission gates;
  `identity/entitlements/` — the entitlements engine.
- `infra/feature-flags/registry.yaml` — `default_policy: off` and every surface
  `default: off`; `docs/GOLDEN-RULES.md` — AO-GR-6 (flag-gated OFF by default).
- `docs/QA-GATE.md` — "shell + Python stdlib + PyYAML + pytest"; and the absence
  of any dependency manifest in the repository root.
- `docs/REMOTE-CONTROL-GAP-ANALYSIS.md` §2, §6 — the local-only reach of every
  control verb, and the four standing rules this record's table restates.
