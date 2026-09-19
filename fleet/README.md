# fleet — the session fleet operating model (M26, issue #160)

This repo is the complete end-to-end IT department for any organization
(Enterprise/SaaS): work intake, dispatch, execution, verification, merge,
observability, FinOps and governance, run by agents. This directory is the
session layer that operates it.

**Normative contract:** [`CONTRACT.md`](CONTRACT.md) — the roles, the directive
vocabulary, the message schema and the trust rules. **This file is the runbook:**
bootstrap, mailbox layout, listener loop and day-to-day commands. Where the two
could disagree about *who may say what to whom*, the contract wins; where they
could disagree about *which command to type*, this file wins.

**The principal's way in is its own page:**
[`../docs/OPERATOR-ACCESS.md`](../docs/OPERATOR-ACCESS.md). It names every
surface a principal can use — the A2A control channel (the **primary** control
plane, §7.1 of the contract), this file's control verbs, `make operator` (the
live view) and `make console` (the browser console, loopback and fail-closed by
default) — with the exact command for each and what needs a shell on the box.
This runbook defers to it for access; it stays authoritative for mechanics.
A principal who has **no** shell on the box starts at §6 of that page: the
remote route (SSH over the existing Cloudflare Tunnel, with Cloudflare Access in
front) that removes that first need. It is flag-gated OFF and publishes nothing
until a principal applies it, so none of the bootstrap below changes while it
ships off.

## The model

| Role | Runtime | Behaviour |
|---|---|---|
| **Principal** | You, at the override terminal | Orders the **director** (`channel.py order`). Never addresses the dispatcher directly — the channel refuses it, because skipping a rung makes the director advisory. |
| **Director** | DSv4PM (DeepSeek v4 Pro Max) with **human override** | The middle rung and the only directive issuer: `fleet/brain.py` drains the principal's orders, signs each one into a directive for the dispatcher, and reports back. |
| **Dispatcher** | DeepSeek v4.1 Flash, **no thinking** (DSv4FNone) | Never picks work: drains `.fleet/inbox`, executes only director directives, spawns epic-focused executors per directive, writes acks/results to `.fleet/outbox`. |
| **Executors** | DeepSeek agent model, tier/thinking chosen by the director's FinOps block | Epic-focused executors. One issue = one executor = one lane, each in its own worktree. |

The chain is enforced by the transport, not by convention:

```
principal ──order──▶ director ──directive──▶ dispatcher ──spawn──▶ executor
   ▲                   ▲                     │                 │
   └──── ack/report ────┴───── ack/report ────┴──── result ────┘
```

```bash
# order the director (the top of the chain, and the ONLY way in)
python3 fleet/channel.py order --message '{"type":"directive","task":{"issue":166,"lane":"session-fleet"},"body":"dispatch one executor"}'
python3 fleet/channel.py brain-outbox           # the director's acks and refusals
python3 fleet/channel.py brain-inbox --timeout-seconds 5   # what the director is working on
```

The director ↔ dispatcher and dispatcher ↔ executor traffic uses this file-mailbox
channel as the transport of record (localhost mechanics, GR-21) — decided and
recorded in [ADR-0011](../docs/decision-records/ADR-0011-session-fleet-transport.md),
which the channel cites. The message contract is
[`schema/message.schema.json`](schema/message.schema.json), which is
authoritative for the envelope's shape; [`CONTRACT.md`](CONTRACT.md) is
authoritative for the roles, the vocabulary and the trust rules, and both are
gated by `make verify`. The hub's A2A product layer (this repo's M9,
#101–#109) is the platform contract this model will graduate onto when it
ships — that graduation is a transport swap, not a rewrite of the contract.

## Bootstrap (the ONLY human step)

1. Open the **dispatcher session** in VS Code.
2. In that session, set the model to **DeepSeek v4.1 Flash** and thinking
   effort to **none** (DSv4FNone). Nothing else is needed from the principal.
3. The dispatcher session reads `fleet/directive.json` (the standing directive)
   and starts draining `.fleet/inbox` — its dispatcher is
   `fleet/channel.py` plus the executor from issue #163.

Everything after step 2 is code: the director sends directives with
`python3 fleet/channel.py send --message <file-or-inline-json>`, the dispatcher acknowledges into
`.fleet/outbox`, executors work the issue (subject to the claim rules in
`governance/dispatch/`), and evidence returns the same way.

## Directives

```json
{
  "schema": 2,
  "from": "director",
  "to": "dispatcher",
  "type": "directive",
  "model": { "tier": "flash", "thinking": "none" },
  "task": { "issue": 162, "epic": 160, "lane": "fleet" },
  "body": "spawn one executor for issue #162 and report back"
}
```

* Only `directive`, `ack`, `result` and `halt` message types exist.
* A directive is also a **chain edge**: the executor authorizes its claim with
  it — `python3 governance/dispatch/cli.py claim --issue N --agent X --lane L
  --directive <id>` — so the director can legally direct off-frontier work while
  unordered scavenging stays refused.
* Only the director may address a directive to the dispatcher; the dispatcher can never
  issue directives (dispatcher-never-picks-work rule, enforced).
* Every directive is stamped with a `nonce` when it does not carry one, and
  `send` refuses a nonce it has already seen — a replay never overwrites a
  queued order (contract §3).
* `model.tier` ∈ {`pro`, `flash`} and `model.thinking` ∈ {`none`, `low`,
  `high`}; anything else is refused. An executor cannot raise its own tier —
  only a new director directive may escalate.

## Enterprise instruction stack

Every director directive hands the executor the **same enterprise instruction
stack the principal's top-level agent runs under**, not just the repo's own
docs. `fleet/brain.py` combines three KB groups into the directive's
"Read first" block:

* `kb.own_repo` — repo-relative doctrine (`AGENTS.md`, `docs/GOLDEN-RULES.md`,
  `docs/EXECUTION-PLAN.md`, `docs/ARCHITECTURE.md`, `fleet/CONTRACT.md`,
  `.board/snapshot.json`);
* `kb.enterprise_instructions` — the principal's home-relative instruction stack
  (`~/.claude/*`, the VS Code user prompts, `~/deepseek/{AGENTS,CLAUDE}.md`,
  `~/cmr/{AGENTS,GOLDEN-RULES}.md`, the key `~/cmr/docs/*.md`, and all 11
  `~/.copilot/agents/*.agent.md` SME profiles). Each `~` is expanded to a real
  path by `fleet/brain.py` so an executor can open it without a shell;
* `kb.fleet_modules` — the fleet-module pointers, kept last as provenance.

The paths live in `fleet/profiles/brain.profile.json`
(`kb.enterprise_instructions`) and are expanded in `fleet/brain.py`.

## Push → wait → completion trigger

The director never spins a session on a task. It pushes, blocks, and wakes:

```bash
# director
python3 fleet/channel.py send --message /tmp/directive.json      # prints the message id
python3 fleet/channel.py wait --id <id> --timeout-seconds 600    # blocks

# `--message` also takes inline JSON, so a live terminal needs no temp file:
python3 fleet/channel.py send --message '{"from":"director","to":"dispatcher","type":"directive","correlation_id":"x","task":{"issue":5}}'

# executor (the dispatcher), the moment the task is done:
python3 fleet/channel.py report --from dispatcher --correlation <id> --type result --body "merged #171"

# the waiting director prints the result and continues — that is the A2A trigger
```

`wait` matches the outbox by message id **or** `correlation_id`, so an executor
answers the exact directive that is being waited on. A timeout exits 1 (NOT-OK)
— a silent pass would be a false green. The dispatcher (#163) calls
`report` on completion; ack/result messages must carry their `correlation_id`
and may never come from the director itself (enforced by the contract and the
gate).

## Dispatcher listener loop (the pulse)

The dispatcher has no agency of its own — it runs the listener and acts on whatever
it prints:

```bash
while true; do
  python3 fleet/channel.py watch --timeout-seconds 600   # blocks until an order arrives
  # ...execute exactly what it printed...
  python3 fleet/channel.py report --from dispatcher --correlation <id> --type result --body "<evidence>"
done
```

`watch` exits 1 (IDLE) when nothing arrives inside the window — that is a signal
to run it again, not an error. Reporting **consumes** the directive: it leaves
`.fleet/inbox` and lands in `.fleet/done`, so the pending count is always the
number of outstanding orders. Without a running listener, queued directives sit
unread — which is exactly what a timed-out `wait` on the director side means.

## Escalation + the live log (both sides idle, never asleep)

The director and dispatcher never sleep — they idle and wait to be pinged:

```bash
# dispatcher/executor: when a directive hits trouble, raise it instead of going dark
python3 fleet/channel.py escalate --from dispatcher --correlation <id> \
  --severity critical --body "verify failed twice: <exact error>"

# director: idle-watch the whole channel as a live log
python3 fleet/channel.py listen --timeout-seconds 0
```

`listen` tails `.fleet/slog.jsonl` (the append-only structured log every
`send`/`report`/`escalate` writes) and prints each message the moment it lands,
so an escalation from the dispatcher pings the director terminal in real time. The
director stays blocked on `listen` — idle, not asleep — and answers on demand.
Escalations must carry `correlation_id` and a `severity` (`info`/`warn`/
`critical`) and may only come from the dispatcher or an executor, never the director
(enforced by the gate).

## Live logs, KB access and mid-run steering (the A2A extension, issue #367)

The mailbox above carries *discrete* messages. Issue #367 adds three live
capabilities over the same transport — still localhost, file-based, no
daemons:

```bash
# follow one run's stdout/events live (per-directive log stream)
python3 fleet/channel.py follow --directive <directive-id> --timeout-seconds 0

# append one event to a run's stream (the loop does this for you)
python3 fleet/channel.py log --directive <directive-id> --line "checkpoint: ..."

# pull the KB mid-run — a running agent answers from the recorded catalogue
python3 fleet/channel.py kb --text "fail-closed" --kind policy --limit 5
python3 fleet/channel.py kb --json --text "lessons" --limit 3

# steer a stuck run without killing or re-dispatching it
python3 fleet/channel.py steer --directive <directive-id> \
  --body "the gate is green in the worktree; the failure is a stale snapshot — refresh and re-run"

# the principal steers through the hierarchy, never directly:
python3 fleet/channel.py order --message '{
  "type": "directive", "task": {"kind": "steer", "directive": "<directive-id>"},
  "body": "retry against the refreshed board snapshot"}'
```

- **`follow`** tails `.fleet/runs/<directive>.log` — every stdout line the
  executor writes (streamed by the loop as it happens) plus the loop's own
  events (dispatch, claim, lane, steer, verdict). `--timeout-seconds 0`
  follows forever; `--max-lines` bounds it.
- **`kb`** answers from `governance/knowledge/catalog.json` (the recorded
  knowledge index), each hit with source-backed evidence; a missing catalogue
  is CANNOT-ASSESS (exit 2), a query with no matches is NOT-OK (exit 1).
- **`steer`** queues a director-signed hint for an in-flight directive. The
  dispatcher loop delivers it every cycle: the hint goes to the running child's
  stdin, is echoed into the run's log stream and stamped into the run marker
  (`.fleet/runs/<id>.json` → `steered`), then the steer is consumed. A steer
  whose run has not started stays queued; one whose run already finished is
  dropped, never re-targeted. Nothing is killed or re-dispatched.

## Control plane (refresh / update / poke / halt / debug / watch / health)

From the director/human terminal — without stopping the dispatcher loop:

```bash
python3 fleet/control.py refresh    # git pull --ff-only + snapshot + make verify
python3 fleet/control.py update     # refresh + rebuild the knowledge index
python3 fleet/control.py poke       # ping the dispatcher: it acks (liveness)
python3 fleet/control.py halt       # stop the dispatcher loop cleanly
python3 fleet/control.py debug      # channel + board + slog tail + loop process
python3 fleet/control.py watch      # idle-watch the slog (same as listen)
python3 fleet/control.py health     # tri-state signal: 0 healthy / 1 degraded / 2 failing
```

`health` (issue #163) is read-only: it never spawns an executor or touches the
mailbox. It reports `2 failing` when `fleet/terminal.py` is not running at
all, `1 degraded` when the loop runs but `.fleet/slog.jsonl` has gone stale or
a claim is wedged past the staleness window, `0 healthy` otherwise. Run it
directly for the raw JSON and exit code: `python3 fleet/health.py check
[--stale-minutes N]`.

`refresh` on the dispatcher side is a **self-update**: the loop pulls, runs
`make verify`, then re-executes itself with the new code — so the terminal can
be upgraded live without a human restart. `poke` forces it to react so you can
debug it without stopping it.

## Launch the two sessions

One command starts whichever rungs are missing and puts you in front of the whole
fleet:

```bash
bash fleet/run-fleet.sh        # == python3 fleet/control.py live
```

Or separately:

```bash
bash fleet/brain.sh            # director: the context stream + the order loop
bash fleet/terminal.sh         # dispatcher: never-idle loop (watch -> run -> report/escalate)
```

The dispatcher loop (`fleet/terminal.py`) is code-native: it watches `.fleet/inbox`
forever, runs one executor per directive with the agent CLI (`--runner "claude
-p"` by default; `--dry-run` prints the command), reports the result, and
escaluates any failure. An empty inbox is just another poll cycle — it never
idles out. `FLEET_RUNNER` overrides the runner.

### The runner preflight (issue #733)

The loop resolves its runner **in code**, before it reads the inbox, every cycle:
`resolve_runner` looks the executable up on PATH and then in the documented
per-user install directories (`~/.local/bin`, `~/bin`, `~/.claude/local`, …,
derived from HOME — `fleet/runtime.py`), and `argv[0]` is handed to the child as
an **absolute** path. This is not belt-and-braces: the loop is cron's child and
inherits cron's minimal PATH, and the measured failure was the fleet being unable
to spawn a single executor because `~/.local/bin` was not on it.

The same environment is passed explicitly at spawn (`fleet/watchdog.py`), so the
whole chain — cron → watchdog → launcher → loop → executor — sees one PATH.

An unresolvable runner is a **startup condition, not a per-directive failure**:
the loop escalates **once**, holds the queue via `.fleet/paused` (so every control
is still read — the hold is released automatically the moment the runner
resolves), and dispatches nothing. The hold is recorded in
`.fleet/runner-hold.json`, so the preflight never releases a pause a principal
set.

A gate the loop could not assess is **CANNOT-ASSESS**, never a failure of the
work: a timed-out `make verify` (the gate of record's budget is 1800s) is reported
as CANNOT-ASSESS at `warn`, because escalating it `critical` is what re-dispatched
the directive every cycle.

### The runner capability contract (issue #841)

Resolving the runner is only half the question. A runner is a **(binary, argv shape,
model vocabulary, required environment)** quadruple, and a dispatch can be honoured
only when all four agree — `fleet/runners.py` is where that is declared and checked.

Measured 2026-09-15 (`master` @ `dd8cbfc`): the loop asked whether `claude` resolved
(it did) and never whether it could honour `deepseek-v4-flash` (it could not), so
every executor run died about ten seconds after it started:

    [claude-code:unrecognized_model] {"model":"deepseek-v4-flash","query_source":"sdk"}
    .fleet/runs/*.log: 192 model-rejection lines, 40 status=failed, 0 status=ok

`deepseek-v4-flash` is DeepSeek's own model id and `claude` knows only its own
aliases and `claude-*` names; the BYOK environment that reconciles the two was
unset, and nothing said so. Three of the four parts were individually fine and the
dispatch still could not work — while the watchdog reported both rungs healthy.

Two profiles are declared:

| profile | binary | model switch | requires |
|---|---|---|---|
| `claude-byok` (default) | `claude` | `--model` | `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN` |
| `deepseek-native` | `deepseek` | `-m` | nothing — it carries its own credential config |

The profile is inferred from `argv[0]`'s basename. A principal whose runner is a
wrapper — or who wants a non-default profile — names it:

```bash
FLEET_RUNNER_PROFILE=claude-byok   # which contract this command line implements
FLEET_RUNNER=deepseek              # and/or which runner to use
```

A runner that cannot honour the dispatch is a **startup condition, exactly like an
unresolvable one**: the loop escalates **once**, holds the queue, and dispatches
nothing — rather than dispatching a directive that dies and is only recorded as
`status=failed` in a run log. The two checks are ordered (resolve, then capability)
and either one alone holds the queue.

What the check can and cannot prove: it is offline and cheap, so it proves the
**declared contract is satisfied** — a required variable that is unset is refused
**by name**, with the remedy in the message. Whether an endpoint actually accepts a
model id is a network question it deliberately does not ask, and `make verify` must
not need the network.

> `deepseek-native` is declared, not recommended: the contract is proven here, but
> that this runner completes a *lane's* work (tools, edits, a PR) is **unproven** and
> is not asserted. A real dispatch is what would prove it.

## Log into the live session

`live` (alias `attach`) is the principal's way in: it starts only the rungs that
are missing — the same rule `start` follows — and then attaches to a tmux session
named `fleet`.

```bash
python3 fleet/control.py live              # or: bash fleet/run-fleet.sh
python3 fleet/control.py attach            # the same verb by its other name
python3 fleet/control.py live --dry-run    # print the tmux commands, build nothing
```

The session has one window per thing you need to watch, and **the session is a
view, not the host**: the rungs run detached (the watchdog and cron own their
lifecycle), so attaching, detaching or even killing the session never touches a
run in flight.

| window      | runs                                        |
|-------------|---------------------------------------------|
| `dashboard` | `python3 fleet/console.py`                   |
| `brain`     | `tail -f .fleet/brain.log`                   |
| `sister`    | `tail -f .fleet/sister.log`                  |
| `monitor`   | `tail -f .fleet/monitor.log`                 |

Detach with `Ctrl-b d`; the fleet keeps running. Attaching again reuses the
existing session — it never rebuilds it over a live fleet (the singleton guard
would refuse the new panes and you would see `[exited]`).

### Where each rung's stream goes

Every rung is respawned detached with its stdout+stderr **appended** to
`.fleet/<rung>.log` — `brain.log`, `sister.log`, `monitor.log` — by the watchdog
when it respawns a rung and by `control.py start` when it starts one. The windows
above tail exactly those files. No tmux? The same view is one command:

```bash
python3 fleet/console.py           # self-refreshing dashboard (Ctrl-C exits)
python3 fleet/console.py --once    # a single frame, for a script or a log
```

The frame carries: the header (repo, HEAD, time); each rung (pid, state, running
commit, heartbeat age); the orders waiting for the director and the latest one; the
director's latest acks; the live claims; wave progress with `✓` closed, `▶`
dispatched and `·` pending; the last events from `.fleet/slog.jsonl`; and the last
verdicts from `.fleet/watchdog.log`.

### What the director prints

The director's stream (`brain.log`, and the `brain` window) is the only place the
middle rung explains itself. It is deliberately low-noise — a banner, the order
it received, what it did with it, the waves it advanced, and a heartbeat while
idle:

```
[brain] up | orders pending=0 | dispatched=11 | waves: #219=[232] | claims=0 | HEAD=8d9c219 | watchdog=healthy
[brain] order o-1 (#232) | task={"issue": 232, "lane": "fleet"} | body=micro-task of #219
[brain] o-1: → dispatched #232 at flash/none — channel send: OK — <id> queued for the sister
[brain] → advanced waves: dispatched [233, 234]
[brain] idle 30s | orders pending=0 | dispatched=11 | waves: #219=[232] | claims=0 | HEAD=8d9c219 | watchdog=healthy
```

A refusal reads the same way, with the reason the contract gave:
`[brain] o-1: → refused: order names no issue (...)`.

## Status report (paperclip planning / status-report discipline)

`fleet/console.py` answers *what is the fleet doing right now*. `fleet/report.py`
answers the planning question underneath it — *where does this stand, and what is
in the way* — and it answers it in the shape the vendored **paperclip** module
declares: `roadmaps` ("plan-first artifacts and roadmap tracking for workstreams
and milestones") and `status-reports` ("project status and delivery reporting
patterns with crisp, human-readable output"). The pattern source is
`vendor/CMR/catalog/modules/paperclip` and the owning registry persona is
[`paperclip`](../registry/personas/cards/paperclip.yaml) (tier LOW, lanes
`paperclip` / `knowledge`) — the reporting half of the ownership boundary
[ADR-0012](../docs/decision-records/ADR-0012-hermes-paperclip-boundary.md)
records. The header the report prints names both, so a reader never has to guess
which pattern a line follows.

```bash
python3 fleet/report.py            # the human-readable report
python3 fleet/report.py --json     # the same report, typed and structured
```

Above the sections the report prints the **active epic** block — the one epic the
fleet is focused on (`.board/focus.json`, `governance/dispatch/focus.py`): the
epic number, its per-epic progress as children `closed/total`, the pooled queue of
open non-epic work outside it, and the effective agent count the focus declares.
`max_agents: 0` is labelled **the pool** rather than resolved into a number — the
capacity formula belongs to dispatch (lane F3/#718), not to the report. The block
is built by a pure function over an injected board snapshot and focus, so it is
asserted from a fixture (`fleet/tests/test_report_epic.py`) with no fleet and no
network.

Four sections, in dependency order, each item carrying its issue, its lane and an
**evidence pointer** — a run id, a claim, the child's own `Verify:` command, or the
closing record:

| section       | what it holds                                                 |
|---------------|---------------------------------------------------------------|
| **now**       | a run marker a loop is tracking, or a live claim               |
| **next**      | wave children whose dependencies are met, behind the frontier  |
| **blocked**   | work that cannot proceed, named with what it waits on          |
| **delivered** | terminal outcomes the fleet already recorded                   |

It reads only state the fleet already writes — the wave plans plus child issue
states, the live claims the dispatch ledger folds (`governance/dispatch/cli.py
status` prints the same set), the in-flight run markers under `.fleet/runs/`, the
recent `.fleet/slog.jsonl` outcomes, the board milestone/frontier through the
same `governance/dispatch/order.py` rule the claim gate enforces, and the pinned
focus (`.board/focus.json`) the active-epic block resolves. **It is
read-only:** it never writes `.fleet/`, never claims, releases or reaps, and can
never change a dispatch decision.

Exit codes follow the repo's tri-state convention:

| code | meaning                                                             |
|------|---------------------------------------------------------------------|
| `0`  | OK — the report was produced and nothing in it is blocked            |
| `1`  | NOT-OK — the report was produced and at least one item is blocked    |
| `2`  | CANNOT-ASSESS — no wave plan and no board snapshot: nothing to report on |

`2` is a refusal, not an empty report: with no plan to derive from, `report.py`
prints no report at all rather than inventing one. The report also states its own
**evidence basis** — the commit it was read at, the milestone and frontier, every
source actually read with its record count, and the last `make verify` verdict
from `.verify/attestation.json` when the tree carries one (and says so plainly
when it does not). `--json` is a first-class mode, validated against the report's
own schema before it is printed, so a consumer gets a shape guarantee rather than
a hope. The suite is `fleet/tests/test_report.py` — pure functions over a
synthetic fleet, no network and no tmux.

## Mailbox

```
.fleet/inbox/    pending directives for the dispatcher   (runtime, gitignored)
.fleet/sent/     the director's copy of what it sent    (runtime, gitignored)
.fleet/outbox/   acks and results back to the director  (runtime, gitignored)
.fleet/done/     directives answered and consumed   (runtime, gitignored)
.fleet/runs/     run markers + each run's live log stream (`<id>.log`)  (runtime, gitignored)
.fleet/brain/steer/  the director's pending steering hints, one per directive  (runtime, gitignored)
.fleet/attempts/     one persisted attempt budget per directive  (runtime, gitignored)
.fleet/dead-letter/  the terminal artifact of a retired directive  (runtime, gitignored)
```

`fleet/directive.json` and the schema are tracked artifacts — the dispatcher can
always discover its standing orders from a clean clone.

## Dead-lettering a directive by protocol (`control:drop`)

A directive the fleet cannot execute must not be retried for ever. There are two
ways one becomes **terminal**, and they share one implementation
(`runaway.dead_letter`) and one record shape, so they can never disagree:

1. **Automatically** — the loop exhausts the directive's attempt budget
   (`.fleet/attempts/<id>.json`, `AO_RUNAWAY_ATTEMPTS`, default 5). Its held
   paths (a refused claim, a run that did not land, a self-heal, an in-flight
   hold, an untracked foreign claim) all count against that one counter.
2. **By protocol** — a principal, or a peer agent that can see the order is
   wedged, says so **over the control channel**:

```bash
python3 fleet/control.py drop --directive <id> --reason "its work already landed"
python3 fleet/control.py dead-letter                       # list the mailbox
python3 fleet/control.py dead-letter --directive <id>      # inspect one record
```

The verb orders the **director**, which issues the control to the dispatcher exactly
like every other lever; the dispatcher retires the named directive, **acks** the
sender naming what it dropped, and the order is moved to
`.fleet/dead-letter/<id>.json`. `channel watch` will never return it again.

**Out-of-band `mv` is legacy remediation and is superseded by the verb.** Moving
`.fleet/inbox/<id>.json` aside by hand (the 23 directives swept to
`/tmp/dead-directives/` on 2026-09-14) *works*, but it races the loop's
mid-`watch` reader, loses the attempt history and the reason, bypasses the
channel so there is no ack and no audit record, and **cannot be done by an agent
at all** — an executor that detects its own directive is wedged has no way to say
so. Use the verb; it is the same lever the automatic path uses. To revive a
retired order after fixing the cause:
`python3 fleet/runaway.py rearm --directive <id>`.

## Recovery (no principal step — code handles each failure)

Every failure mode below is handled by an existing command; none of them needs
a human to do anything but run the command (or nothing at all, if the loop
self-heals).

* **The dispatcher dies mid-claim.** A claim carries a `ttl_hours` (24h default,
  set at claim time by `governance/dispatch/cli.py claim`). Once the TTL
  elapses the claim is stale and `governance/dispatch/cli.py claim` for the
  same issue on a new agent **reaps** it automatically (`reaped_agent` in the
  claim event names who was taken over from) — no manual unlock step. A new
  dispatcher session just claims again; there is nothing to clean up by hand.
* **The mailbox fills up (`.fleet/inbox` backs up).** `report` **consumes**
  the directive it answers — it moves the message from `.fleet/inbox` to
  `.fleet/done`, so a backlog only means the listener stopped running, not
  that state is corrupted. Restarting the loop (`bash fleet/terminal.sh`, or
  `python3 fleet/control.py refresh`) drains the backlog from where it left
  off; nothing needs to be deleted or re-sent.
* **The dispatcher loop fails (rc=2).** `fleet/health.py check` returns `2
  failing` exactly when `fleet/terminal.py` is not running at all (see
  below) — that exit code is itself the alert: the director's `listen`/`watch`
  loop or any script polling `health` sees `2` and knows to restart the
  terminal (`bash fleet/terminal.sh`) rather than guess. `1 degraded` means
  the loop is up but stale (`.fleet/slog.jsonl` hasn't moved, or a claim is
  wedged past the staleness window) — `python3 fleet/control.py poke` forces
  it to react without a restart.

## Cron-owned fleet (the watchdog)

The fleet survives a reboot or a crashed loop without a human: one crontab
line owns the director/dispatcher/monitor rungs. Every N minutes it runs
`fleet/watchdog.py run`, which respawns a **missing**, **stale** or **drifted**
loop rung, restarts the **monitor** when it is missing, and does nothing when
the fleet is healthy — so a tick is cheap and idempotent. A run in flight is
never restarted just to update code (the one rule the watchdog never breaks), and
the HOLD that enforces it is itself bounded — see *the crash-loop escape* below.

### Two different drift faults, two different remedies (AO-GR-21, issue #773)

The running commit differing from `origin/master` is *one* fact; what to do
about it is another, and getting that wrong made the watchdog a runaway. The two
cases are named separately because their remedies differ:

* **`drifted`** — the rung's commit is **not** the local checkout's HEAD either,
  so the rung itself is stale: **respawn** (it re-executes the checkout, which
  already holds the merged code) is the right remedy.
* **`checkout-behind`** — the rung's commit **is** the local HEAD while
  `origin/master` is ahead: the rung is current *relative to the checkout* and
  the **checkout** is stale. A respawn re-executes that same stale checkout and
  therefore **cannot** change the value being compared; the remedy is a
  **fast-forward** (`git fetch` + `git merge --ff-only`), after which one respawn
  loads the new HEAD. A diverged checkout is refused by name, never force-moved.

Measured 2026-09-15, this distinction missing: **132 `drifted … — respawned`
decisions, 45 `stopping cleanly` cycles, a director process never older than 60s,
and no work done at all.** The detection was right (AO-GR-25); the remedy was an
action that could not change what was compared, repeated without bound —
AO-GR-21's own lesson, applied to the watchdog itself.

### The remedy is bounded (AO-GR-21, issue #773)

Every acting path is recorded per rung under `.fleet/watchdog/` and bounded:

* a remedy that does not change the observed state is attempted at most
  **`AO_WATCHDOG_RESPAWN_ATTEMPTS`** times (default **3**), spaced by
  `min(AO_WATCHDOG_RESPAWN_BACKOFF * 2**(n-1), 300)` seconds (base default
  **60**) — the same contract harvested from the CMR hub's `ops/retry.sh` that
  `fleet/runaway.py` uses for a directive;
* when the cap is exhausted the watchdog **escalates once** — naming both
  commits and the checkout path, and writing
  `.fleet/watchdog/escalations/<rung>.<first-seen>.json` — and **parks** the rung:
  it is reported on every pass and **never retried** until a principal rearms it;
* a remedy that *works* resets the counter, so a healing fleet is never parked;
* an unusable knob value **refuses the pass** (exit 2) instead of silently
  disarming the cap;
* a **busy** rung is never restarted to update code, but the finding is no longer
  dropped: it is recorded as **pending drift** and acted on by the first pass
after the run completes (previously it logged `left alone` on every tick,
forever).

```bash
python3 fleet/watchdog.py rearm --rung brain   # clear a parked record after fixing the cause
```

`bash scripts/check-watchdog-bounded.sh` drives the real module: it names both
cases, fast-forwards a REAL scratch repository, measures the attempt counts, drives
the REAL marker judgement against REAL marker files (the probes above stub
`run_in_flight`, which is exactly why a pid-only discriminator stayed invisible
here), and mutation-proves itself with four mutants of the real source (the cap
removed, the local-HEAD distinction removed, the crash-loop escape removed, and the
marker-evidence rule removed) — each required to diverge on a probe whose value
must change, with the mutation's landing proved by sha256.

### What "a run in flight" means — the marker's own evidence (issue #366, #793)

The one rule the watchdog never breaks — *never restart a run just to update code* —
is enforced by asking whether **the marker's own evidence** says a run is in flight.
It used to ask whether the marker's `pid` was alive. That `pid` is the **loop's**, and
a loop outlives every run it dispatches, so a crashed run left a marker that read as
"in flight" for as long as the loop lived. Measured 2026-09-14: three markers ~4.5h
old, every one with `child_pid: null`, each naming the live dispatcher loop's pid — so
the dispatcher's drift lock was held open on every tick while it executed code that
predated five merged fixes.

Flight is exactly two things, and `governance/spawn/liveness.py` is the one place
that decides them:

* a **live `child_pid`** — the executor itself, running; or
* a **beat no older than `AO_RUN_STALE_SECONDS`** (default **120**) — the run's own
  beater advanced it, which a crashed run cannot do. This is also what protects a run
  that has only just started, before any child exists (`mark_run` writes
  `child_pid: null` first).

A marker with neither is a **crashed run**, and the drift remedy proceeds through the
ordinary bounded path. A **missing or unreadable** beat is judged the same way, and
deliberately: both writers store the stamp atomically, so an unreadable `ts` means a
corrupted or foreign marker that nothing in the fleet will ever advance, and counting
it as work would restore the very deadlock this removes. The decision is reported with
the marker it was made on (`held by <marker> (live child pid N)`), so an
unattributable hold is impossible.

### The hold is bounded too — the crash-loop escape (issue #366)

The hold is a *policy*; making it a *bound* is the other half. A rung whose runs die
before they can report presents flight on **every** tick, so the hold is re-taken on
every tick and the drift lock never opens —
`drifted … a run is in flight — left alone` on each tick, from 21:32 onward, while
the director was respawned in the same tick.

So the hold gets a budget of its own, counted in **respawns due**: every tick the rung
is drifted (or `checkout-behind`) and the remedy is reached, whether it *runs* or is
*withheld* because a run appeared in flight. `CRASH_LOOP_RESPAWNS` (**3**) of those
inside `CRASH_LOOP_WINDOW_SECONDS` (**600**s), with the observation never moving, is a
crash loop, and the hold stops being honoured:

```
[watchdog] sister: drifted (running 592b132, origin/master 84afa90) — respawned (attempt 1/3) — crash-looping: 3 respawns due within 600s and none of them moved it (N=3) — the drift lock cannot clear, so this respawn is NOT withheld
```

Both constants are named on the line, because a bound whose numbers are not in the log
cannot be audited by the principal reading it. Three properties keep it honest:

* **it is not a second unbounded path.** The escape takes the ordinary remedy route,
  so the attempt cap, the backoff, the escalate-once and the park all still apply —
  the same budget as any other drift;
* **progress resets the ledger**, so a healing fleet can never arm it, and **holds
  spread wider than the window** never arm it either: it is a window, not a lifetime
  tally;
* **a parked rung is excluded first.** A park is terminal (#773), and a *deferral*
  must not be able to un-park it — without that guard the first tick whose ledger had
  aged out of the window took the hold again, flipped the record back to `pending`,
  and would have resurrected a crash loop that had already been escalated and parked.
  That was found by this gate's own probe, not by review.

### What "drifted" is measured against (AO-GR-25, issue #739)

The baseline is **`origin/master`** — never the shared checkout's HEAD. This
matters more than it sounds. The shared checkout is routinely *behind* (it is
wherever a human or a lane last left it), so comparing a loop's commit to it can
compare **stale-to-stale**: the loop's own start commit reads back as the
baseline it is judged against, and a loop executing pre-fix code reports
`healthy`. That was measured on 2026-09-14 — the dispatcher loop (pid 17797, started
19:18Z) ran code from before a fix that merged at ~23:00Z, and the watchdog
logged `sister: healthy` on every tick.

Two consequences worth knowing:

* **`origin/master` here is the already-fetched remote-tracking ref — the
  watchdog does not fetch.** A tick runs every 2 minutes; a fetch per tick would
  put the network on the critical path of a pass that is otherwise local, and
  would have to either slow the fleet or swallow its own failure. Reading
  `refs/remotes/origin/master` costs nothing and cannot fail open. The ref is
  refreshed by the lanes: every lane fetches before it cuts a worktree, so it
  tracks the remote as closely as the fleet actually pulls. The tradeoff is that
  a fix merged **after** the last fetch is invisible until the next one — which is
  exactly why the watchdog line prints the baseline it used, so a principal can
  see how far behind the comparison is rather than trusting a bare `healthy`.

* **An unreadable baseline is `cannot-assess`, and the pass exits 2.** It is
  never folded into `healthy`. The pre-#739 rule was guarded by
  `head != "unknown"`, so an unreadable HEAD *silently disabled drift detection
  entirely* — a control that fails open, which is worse than no control.

```
[watchdog] brain: healthy (running 47a068b, origin/master 47a068b)
[watchdog] sister: drifted (running 592b132, origin/master 47a068b) — respawned (attempt 1/3)
[watchdog] sister: checkout-behind (the checkout is behind: running b95a8b7 = local HEAD, origin/master 37f87f9) — fast-forwarded the checkout (fast-forwarded b95a8b7 -> 37f87f9) and respawned so the rung loads 37f87f9 (attempt 1/3)
[watchdog] monitor: healthy
```

Exit codes are the repo's tri-state: **0** every rung healthy, **1** a definite
failure (`RESPAWN FAILED`, a `CAPABILITY STALE` rung, or a drift whose remedy did
not resolve it — including the escalated, parked rung), **2** CANNOT-ASSESS — no
readable baseline, so the comparison could not be made, or an unusable remedy
budget. A known failure outranks an unassessable one.

`bash scripts/check-fleet-drift.sh` proves all of this against the real
classifier (a mutation-proof pair restores the local-HEAD baseline and the
fail-open guard, and requires each to be caught).

Every rung it respawns is started detached with stdout+stderr appended to
`.fleet/<rung>.log` — the capture the `brain`, `sister` and `monitor` windows of
the live session tail, and the only reason the director is observable at all (it
used to be spawned into `/dev/null`).

```bash
python3 fleet/cron.py install [--interval 2]   # add the crontab line (replaces an existing one)
python3 fleet/cron.py status                    # installed? recent watchdog log
python3 fleet/cron.py run                        # one watchdog pass, now (non-destructive)
python3 fleet/cron.py respawn                    # force-respawn both rungs
python3 fleet/cron.py disable / enable           # toggle the line without deleting it
python3 fleet/cron.py uninstall                  # remove the line
```

The line is identifiable by its `# ao-fleet-watchdog` marker, so `uninstall`
removes exactly this job and `status`/`disable` act on it alone. The same
subcommands are reachable from the control plane:
`python3 fleet/control.py cron <sub>`.

`AO_FLEET_PYTHON` (default `/usr/bin/python3`) sets the interpreter every
managed line is rendered with, and `AO_FLEET_CRON_PATH` (unset by default)
emits a single `PATH=...` line at the top of the managed block — issue #1370,
for a host whose rungs need a venv interpreter or `gh`/`gcloud` off the
default `PATH`; `status` reports drift by name if the installed crontab no
longer matches either variable's current value.

## Fleet monitor (the third rung)

`fleet/monitor.py` is the cron-owned, change-only progress watcher. It polls
every 20s and appends one timestamped line to `.fleet/open-eye.log` only when
the fleet's observable state changed since the previous tick — dispatcher/director
state, held claims, the wave dispatch list and git HEAD — and rewrites
`.fleet/monitor.heartbeat.json` with a JSON liveness beat (`pid`, `state`,
`commit`, `ts`) every tick, in the same shape the director and dispatcher publish, so
the console reads all three rungs with one code path. It exits cleanly on
SIGTERM, and the watchdog restarts it whenever it is missing, so the monitor
is durable and self-healing rather than a one-off runtime script.

```bash
python3 fleet/monitor.py              # the resident monitor (normally started by the watchdog)
tail -f .fleet/open-eye.log           # the change-only progress log
cat .fleet/monitor.heartbeat.json     # the monitor's liveness beat (JSON)
```

All monitor output lives under the gitignored `.fleet/` directory; the module
itself is tracked.

## Fleet-health export (issue #498)

`fleet/health_publish.py` is the fleet's own exit: it renders the verdicts the
fleet computes about **itself** — rung state, beat age, reconciliation outcomes,
watchdog verdicts — as monitoring events, in the transport
[`ADR-0022`](../docs/decision-records/ADR-0022-telemetry-exposition-authority-split.md)
fixes (OTLP/HTTP push, plane-owned endpoint, **flag-gated OFF**).

The machine shape is **declared in-tree** and enforced at construction
(`fleet/health_signals.py`, plus `fleet/schema/health-signal.schema.json`):

| Signal | Labels | Value | Verdict source (imported, never re-declared) |
|---|---|---|---|
| `fleet.rung_state` | `rung`, `state` | 1 | `watchdog.decide` — `healthy` / `stale` / `drifted` / `checkout-behind` / `missing` |
| `fleet.rung_beat_age_seconds` | `rung`, `bucket` | seconds | `channel.heartbeat_age_seconds` — `fresh` / `stale` |
| `fleet.reconcile_outcome` | `outcome` | count | `governance/reconcile` sweep report |
| `fleet.watchdog_verdict` | `rung`, `verdict` | 1 | `channel.capability_finding` (#319) |

Every signal also carries the constant `service` and its own `signal` label — the
only seven label keys are `service`, `signal`, `rung`, `state`, `bucket`,
`outcome`, `verdict`. **A session id is refused by name**: ADR-0022 D5 and the
vendor's `kushin77/monitoring-stack#178` drop that dimension, so the family
publishes *counts and closed-set states* and per-lane detail stays in the ticket
and the log.

Three refusals survive the export, each with a mutation-proven test:

- a **stale** beat leaves as `stale`, never as `healthy`;
- an orphan whose unmerged work exists nowhere else leaves as **`shelved`**, never
  as `reclaimed` (`AGENTS.md` rule 17);
- a state that **cannot be established** leaves as `no-data`, never as a
  fabricated `healthy`.

```bash
python3 fleet/health_publish.py plan            # render, send nothing (read-only)
python3 fleet/health_publish.py plan --json     # the exact OTLP/HTTP payload
python3 fleet/health_publish.py push            # needs the flag ON + an endpoint
```

`push` is inert until the surface is promoted **and** the plane has named an
endpoint (`$AO_MONITORING_ENDPOINT`, GR-6: environment or a secret manager). The
promotion entry this surface reads is:

```yaml
surfaces:
  fleet_health_export:
    default: off          # promotion is a reviewed registry change
    promoted: false
    service: control-plane
    description: >
      Fleet-health push exporter (issue #498, ADR-0022): the fleet's own
      verdicts — rung state + beat age, reconciliation outcomes
      (reclaimed/parked/shelved/suspect) and watchdog capability verdicts —
      pushed to the monitoring plane. Closed label set only (no session ids,
      paths, branches or commits). Inert when unconfigured.
```

> **Wiring (lane #499).** This module's tests are not registered in
> `scripts/pytest-suites.txt` yet, and the flag entry above is not in
> `infra/feature-flags/registry.yaml` — both files belong to other lanes
> (#497/#499) this wave. Until the entry exists the surface reads as `off`
> (deny by default), which is why no flag edit was required to ship it inert.

## Runaway + queue-liveness alarm (issue #728)

The runaway that motivated the attempt cap (#723) — 49 concurrent `make verify`
runs on one box — was caught by a human *noticing* it. Detection is a signal,
and a signal has to be an alarm: an excursion that clears itself before anyone
looks is indistinguishable from one that never happened. `fleet/health.py`
therefore reports the work queue's liveness and **latches** a runaway until a
principal acknowledges it. Contract: [`CONTRACT.md`](CONTRACT.md) §9.

```bash
python3 fleet/health.py check   # full signal: rungs + queue + the latch (read-only)
python3 fleet/health.py alarm   # measure the queue, raise + latch; exit 2 when raised
python3 fleet/health.py ack     # acknowledge — the ONLY way the latch clears
```

The signal reads inbox depth, the oldest pending directive's age and the
dead-letter count (via `runaway.inventory`, the same read `channel.py status`
makes). A condition above any cap raises the alarm and persists the latch at
`<fleet>/health/alarm.json`; the next measurement — taken with the excursion
already gone — is *still* raised, and still names the directives and the
worktrees responsible, from the claim's `directive_id` and the lane records
under `governance/isolation`. Only `ack` clears it (and a second `ack` is
refused). The knobs are `AO_RUNAWAY_INBOX_DEPTH` (default 24),
`AO_RUNAWAY_OLDEST_MINUTES` (default 120) and `AO_RUNAWAY_DEAD_LETTERS`
(default 5); an unreadable value is refused rather than silently defaulted.

The alarm is emitted by the gate of record, not only by an ad-hoc command:
`scripts/check-fleet-channel.sh` provokes the condition, proves the latch
survives the excursion and proves `ack` clears it — and **fails** if the alarm
clears itself (mutation-proved).

The culprit's *names* deliberately do not travel as monitoring labels: per-session
identity is refused by name in `fleet/health_signals.py` (ADR-0022 D5), so the
exported family carries counts while the names stay in the local signal, the
latch artifact and the log (see "Fleet-health export", above).

## The gate

```bash
bash scripts/check-fleet-channel.sh   # 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS
```

The check validates `fleet/directive.json` against the contract, runs the
channel's own mutants (unknown message type, bad tier, bad thinking, missing
role, a dispatcher-issued directive) — each mutant must be refused, so the check
cannot pass vacuously — and drives the runaway alarm (§9): raised and naming the
offending directive + worktree, still raised once the excursion is removed,
cleared by `ack`, with a second `ack` refused. A check that cannot fail is a
formality, so each half is proved on a path that genuinely fails.

```bash
bash scripts/check-fleet-runner-preflight.sh   # 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS
```

The runner preflight is gated the same way (#733): the declarations are asserted
by name, the preflight call site is asserted to *precede* the inbox read, and the
REAL loop is driven in an isolated scratch tree with three queued directives and a
runner that is on neither PATH nor HOME — exactly one escalation, nothing
dispatched, the queue held, the work left pending. Two mutants of the real loop
(the preflight neutralised, the hold removed) must each be **detected**, so a
regression cannot pass by looking right.

The runner gate is two ordered checks since #841 (does it resolve, then can it
honour the model), so the preflight mutant removes **both** halves rather than one:
either half alone holds the queue by design, and a mutant that removes one half
leaves the invariant intact.

```bash
bash scripts/check-runner-capability.sh   # 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS
```

The capability contract is gated the same way (#841): the suite runs, the BEHAVIOUR
is probed **both ways** — an unwired pairing is refused and the refusal is required
to name every missing variable, and the same runner with its environment present is
required to be allowed (a check that refuses unconditionally would pass the first
probe and prove nothing) — and the check must be able to fail: `unhonourable` is
mutated to always allow, the mutant is asserted to have **landed** by content hash,
the probe must notice it, and the source is restored byte-for-byte afterwards.

## Restarting a rung: which signal, and why it matters (AO-GR-27)

Both loops install handlers for **`SIGTERM` and `SIGINT` only**
(`fleet/terminal.py:1036`, `fleet/brain.py:262`, `fleet/monitor.py:178`). Those
are *clean* stops: the signal handler releases the in-flight claim and takes its
executor down with it, so the restart suppresses a duplicate rather than
stranding work.

**`SIGHUP` is NOT handled.** On Linux its default action is to **terminate the
process immediately**, so `kill -HUP <pid>` bypasses the handler, the claim
release and the child teardown entirely. It is therefore *worse* than
`SIGTERM` — it is an abrupt kill wearing the costume of a graceful reload. It
also does not do what a `HUP` usually means: neither loop re-reads config or
re-execs on it.

```bash
# The clean restart (the recommended one):
kill -TERM "$(pgrep -f 'fleet/terminal.py run')"

# For comparison, a LIVE self-upgrade without any restart at all:
python3 fleet/channel.py send ...   # a `refresh` control pulls, gates,
                                    # then re-execs the loop with the new code
```

`refresh` is the right tool when the goal is "pick up merged code": the loop
pulls, runs `make verify`, and re-executes itself with the new code. Use
`SIGTERM` when you specifically want the rung to stop and let the watchdog bring
it back.

> **A merged fix does not reach a running loop by itself.** The loop runs the
> code it started with, and the watchdog only replaces it when it is missing,
> stale, or **drifted from `origin/master`** (AO-GR-25). If a fix is merged and
> the fleet is still behaving like the old one, compare the running rung's commit
> against `origin/master` before assuming the fix did not work — and remember the
> watchdog's baseline is the **fetched** `origin/master`, so fetch before you
> compare or you will be reading a stale ref too.

```bash
# What is the running loop actually executing, and what is the baseline?
python3 -c "import sys;sys.path.insert(0,'fleet');import channel;print(channel.head_commit(), channel.remote_head_commit())"
tail -3 .fleet/watchdog.log   # the last pass names both commits per rung

# Force the loop onto current code without a restart: it pulls, gates, re-execs.
python3 fleet/channel.py send ... # a `refresh` control
```
