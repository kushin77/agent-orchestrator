# Session fleet steering contract (envelope schema 2; schema 1 accepted on read)

> **Status:** normative · **Ratified by:** issue #161 (milestone M26, epic #160)
> · **Transport decision:** [ADR-0011](../docs/decision-records/ADR-0011-session-fleet-transport.md)
> · **Role vocabulary:** declared once in
> [`../governance/vocabulary/fleet.yaml`](../governance/vocabulary/fleet.yaml) (issue #777)

This is the **normative** contract for the session fleet operating model: the
roles, the directive vocabulary, the message schema, and the trust rules. It is
enforced, not advisory. `fleet/channel.py` refuses traffic that violates it, and
[`../scripts/check-fleet-contract.sh`](../scripts/check-fleet-contract.sh) —
wired into `make verify` — fails the gate when any element declared here is
removed from this file (GR-12: a rule a gate cannot fail on is a formality).

**The role vocabulary is declared, not implied (issue #777).** The four role
names are WIRE VALUES — the envelope carries them, and `fleet/channel.py` refuses
any sender or recipient outside the closed set — so the closed set has exactly one
authority: [`../governance/vocabulary/fleet.yaml`](../governance/vocabulary/fleet.yaml).
This file renders it for the reader;
[`../scripts/check-fleet-vocabulary.sh`](../scripts/check-fleet-vocabulary.sh)
(in `make verify`) fails when this contract, that glossary, the envelope schema and
the channel's constants stop agreeing, so a future lane cannot mint a third name
by editing one side.

## 0. What is authoritative where

This issue (M26) reconciles a contract with a channel that already shipped
(issue #162, PR #169) and a completion trigger that already shipped (issue #171,
PR #173). This document is **additive**: it does not restate the envelope, and
where the contract and the code could disagree, the code wins and this section
says so.

| Artifact | Authority |
|---|---|
| `fleet/CONTRACT.md` (this file) | **Authoritative** for roles, directive vocabulary, trust rules and the transport decision. |
| `fleet/schema/message.schema.json` | **Authoritative** for the message envelope — field names, required fields and the FinOps allowlists. The envelope is v1. |
| [`channel.py`](channel.py) | **Authoritative** for what is actually enforced (it refuses invalid traffic; the JSON Schema documents the same rules but is not the machine that runs). |
| [`README.md`](README.md) | The **operational runbook** (bootstrap, mailbox layout, listener loop, day-to-day commands). It defers to this contract for meaning and to the schema for shape. |
| [ADR-0011](../docs/decision-records/ADR-0011-session-fleet-transport.md) | **Authoritative** for *why the transport is what it is*. |

**Decision — contract vs. runbook.** `fleet/README.md` is deliberately **not**
the contract. It is written as a runbook for the principal (bootstrap steps, mailbox
paths, CLI recipes) and changes whenever the mechanics change; the contract is
the slower-moving statement of *who may say what to whom*. Splitting them means
the gate can pin the contract's vocabulary without freezing the runbook's
commands. The runbook links here for meaning; this file links there for
procedure.

**Reconciliation — `nonce`.** The issue's schema v1 requires a `nonce` field.
The envelope shipped by issue #162 carried `id` (a channel-stamped uuid4) but no
`nonce`. Issue #161 **extends** the envelope rather than rewriting it: `nonce`
is now an optional field accepted by `channel.validate`, stamped by `send` when
absent, and used as an **anti-replay token** — `send` refuses a directive whose
`nonce` is already present in the sent, inbox or done mailboxes. Existing
messages (including the standing directive `fleet/directive.json`) remain valid
without it: the field is additive, and the schema stays v1.

## 1. Roles

The four roles, named for their **function** in the chain rather than by metaphor.
The single authority for the vocabulary is the
[glossary](../governance/vocabulary/fleet.yaml); this table renders it.

| Role (schema 2) | Runtime | Authority |
|---|---|---|
| **principal** | The human, and the override terminal | Orders the director. The top of the chain: it does not address the dispatcher directly, because a chain the principal can skip is not a chain. |
| **director** | Copilot advisor session, maximum DeepSeek vPro | The only directive issuer. Steers, verifies, merges. |
| **dispatcher** | DeepSeek v4.1 Flash, thinking effort **off** (DSv4FNone) | Drains the inbox and executes director directives only, spawns one epic-focused executor per directive, reports results back. It never picks work of its own. |
| **executor** (`executor-<name>`) | Model + tier + thinking chosen by the director's FinOps block | Epic-focused executor. One issue = one lane = one executor. Cannot escalate its own tier. |

**The invariants the rename must not lose.** The retired names carried real rules,
and the rules outlive the words. Each is stated on ONE line so
`scripts/check-fleet-vocabulary.sh` can pin it, strip it, and be refused:

- The dispatcher never picks work; it executes only the directives it was given.
- The director is the only issuer of directives.
- One issue = one lane = one executor.
- The principal orders the director, never the dispatcher.

**Rung 1b — the principal orders the director (added by issue #161's revision).**
Issue #160 named the principal and the director as separate rungs, but shipped no
path between them: the principal's only working trigger was to write into the
dispatcher's inbox, which *is* the director's job. The channel now carries the
missing edge — `channel.py order` (principal → director) and `fleet/brain.py` (the
director loop that signs and dispatches each order to the dispatcher) — and
refuses `principal → dispatcher` outright, because a hierarchy the transport
cannot enforce is a suggestion. Escalations flow the same way in reverse:
dispatcher/executor → director (a problem) and director → principal (a problem the
director has decided it must not resolve alone).

The roster separates **ROLE** (what an agent is for) from **TIER** (which
transport carries it), **MODEL** (what answers) and **EFFORT** (how much
thinking it spends) — harvested from `leaderboard/lib/fleet-roster.sh`, whose
own header records why the separation exists: coupling a seat to a model made a
role's cost and capability drift silently. An executor receives all four in its
directive; none of them is inferred by the executor.

**FinOps block.** Every directive carries `model.tier` ∈ {`pro`, `flash`} and
`model.thinking` ∈ {`none`, `low`, `high`}, plus an optional `budget_hint`.
Anything outside those allowlists is refused, and only a new directive from the
director may raise a tier — an executor that wants more model must ask, not take.

**The retired spellings, kept as a gloss and never as names (issue #777).**
<!-- legacy-gloss:start -->
The names below were the vocabulary before this migration. They are still ACCEPTED
on READ — a schema-1 envelope carrying them validates — for the deprecation window
declared in the glossary, so the contract must go on declaring them: a dialect the
transport still accepts cannot be left undocumented. They are a GLOSS. Outside this
region they are not names, and `scripts/check-fleet-vocabulary.sh` refuses a
retired term used as one.

| Role (schema 1) | Was | Replaced by |
|---|---|---|
| **operator** | The human and override terminal | **principal** |
| **brain** | The advisor session, the only directive issuer | **director** |
| **fleet brain** (the **sister** session) | DeepSeek v4.1 Flash, thinking off — "a dumb terminal" | **dispatcher** |
| **subagent** (`subagent-<name>`) | Epic-focused executor, now `executor-<name>` | **executor** |

The metaphor is kept where it described a real invariant: "a dumb terminal" said
that the dispatcher never picks work, and that rule survives above as a declared
invariant rather than as a word.
<!-- legacy-gloss:end -->

## 2. Directive vocabulary

Exactly six dispatch verbs exist. There is no seventh, and there is no
free-form verb field: each verb maps onto an envelope type that `channel.py`
already enforces, so the vocabulary is expressible in the shipped schema
rather than parallel to it. Issue #367 adds three **live transport
capabilities** on top of this vocabulary — `log`/`follow`, `kb` and `steer`,
declared in §7 — without touching the six verbs here: they are additive verbs
of the channel, not directives.

| Verb | Envelope | Meaning |
|---|---|---|
| `spawn-epic-agent` | `directive` | The director orders the dispatcher to spawn one epic-focused executor. |
| `dispatch-issue` | `directive` | The director names the issue (and lane) that executor works; the directive is the claim's chain edge. |
| `model-directive` | `directive` | The director sets the FinOps block for an executor's run. |
| `handoff` | `directive` | The director moves an in-flight epic from one executor to another; the receiving executor inherits the evidence, not the authority. |
| `halt` | `halt` | The director stops the fleet. Only the director may issue it. |
| `report` | `result` | The dispatcher or an executor answers the directive it was given. A synchronous progress acknowledgement is the same envelope as `ack`. |

## 3. Message envelope — schema 2 current, schema 1 accepted on read

The normative schema is [`schema/message.schema.json`](schema/message.schema.json).
The fields the contract depends on:

| Field | Required | Purpose |
|---|---|---|
| `schema` | no | The envelope version. Absent means `1`: the field is additive, exactly as `nonce` was in issue #161, so every message that predates it stays valid. Schema 2 is current and emits the current role names ONLY; a retired role in a schema-2 envelope is refused by `channel.validate`, BY NAME. |
| `from` / `to` | yes | Roles: `principal`, `director`, `dispatcher` or `executor-<name>` in schema 2; `operator`, `brain`, `sister` or `subagent-<name>` in schema 1. Both dialects are accepted on READ (dual-accept); one dialect per envelope. |
| `type` | yes | `directive` · `ack` · `result` · `halt`. |
| `id` | stamped | Unique message id (uuid4) stamped by the channel. |
| `ts` | stamped | ISO-8601 UTC timestamp stamped by the channel. |
| `correlation_id` | for `ack`/`result` | Binds a report to the directive that caused it — the completion trigger the director waits on. |
| `nonce` | stamped | Anti-replay token; `send` refuses a nonce it has already seen. |
| `model` | for `directive` | The FinOps block: `tier`, `thinking`, optional `budget_hint`. |
| `task` | for work directives | `issue` (required), optional `epic`, optional `lane`. |
| `body` | no | Human- and agent-readable order text. |

## 4. Trust model

Five rules, and one consequence that is itself the rule. They are stated on the
CURRENT role names; the retired spellings of the same rules are kept verbatim
further down, inside a marked legacy region, because schema-1 envelopes are still
ACCEPTED on read for the deprecation window — a dialect the transport still
accepts must stay documented, and a retired term may not appear as a name outside
such a region (`scripts/check-fleet-vocabulary.sh` fails it, by name).

1. **Only the director may issue directives to the dispatcher.** A directive from
   the dispatcher is refused outright: the dispatcher never picks its own work, and
   a seat that can rewrite its own orders is picking work.
1b. **The principal orders the director, and never the dispatcher.** The channel
   validates the sender of every message before it moves, so this is enforced
   rather than requested:

   | Sender | May address | Refused with |
   |---|---|---|
   | `principal` | `director` only, as a `directive` (an order) | *"the principal does not address the dispatcher: it orders the director, and the director orders the dispatcher"* |
   | `director` | `dispatcher` (directives, control) and `principal` (acks, results, escalations) | *"the director takes orders only from the principal"* — a directive addressed to the director from anywhere else |
   | `dispatcher` | `director` (acks, results, escalations) | *"only the director may issue directives to the dispatcher"* |
   | `executor-*` | `director` (results, escalations) | *"only the director may issue directives to the dispatcher"* |

2. **The dispatcher may only spawn executors per a directive.** Spawning is an
   execution of a directive from the director, never a decision of the
   dispatcher's own.
3. **Executors report back through the dispatcher.** An executor's result travels the
   same channel as the directive that caused it, correlated by `correlation_id`;
   it never addresses the director as a peer authority.
4. **Everything else is refused.** Not "discouraged", not "logged" — refused by
   `channel.validate` before the message moves, so an out-of-contract message
   never reaches a mailbox.
5. **Only the director may steer a run mid-flight (added by issue #367).** A
   `steer` is director-signed and correlated to an in-flight directive; the
   dispatcher loop delivers it to the live run without killing or re-dispatching
   it. The principal steers by ordering the director, which relays the
   director-signed steer (`task.kind: steer`) — the chain is never skipped.

Consequences of the model, stated so they are not discovered later:

- The director cannot `ack` or `result` its own directives — a report must come
  from the party that executed.
- Only the director may `halt`.
- An executor cannot escalate its own tier or thinking effort; only a
  `model-directive` from the director changes them.

**The retired rendering of these five rules, verbatim (issue #777).**
<!-- legacy-gloss:start -->
Schema 1 is still accepted on read, so the exact rules a schema-1 reader enforces
stay declared here. They are the same five rules in the retired vocabulary.

1. **Only the brain may issue directives to the sister.**
2. **The sister may only spawn subagents per a directive.**
3. **Subagents report back through the sister.**
4. **Everything else is refused.**

A directive from the sister is refused outright: the sister is a dumb terminal,
and a dumb terminal that can rewrite its own orders is not a dumb terminal. And
only the brain may steer a run mid-flight.
<!-- legacy-gloss:end -->

## 5. Transport decision

The transport is the **file mailbox** (`fleet/channel.py` over
`.fleet/{inbox,sent,outbox,done}`), with the **push → wait → completion trigger**
loop: the director sends a directive, blocks on `wait`, and wakes when the executor
`report`s. The alternatives considered — the hub's A2A product layer, and the
engine's state machine — and the reasons the mailbox is the transport of record
*now* (with A2A as the graduation target once this repo's M9 ships) are recorded
in [ADR-0011](../docs/decision-records/ADR-0011-session-fleet-transport.md).
The channel references that record in its own module docstring, so the transport
and its rationale cannot drift apart.

## 6. Enforcement

| Control | Where | What it proves |
|---|---|---|
| `channel.validate` | `fleet/channel.py` | Refuses out-of-contract traffic at the boundary (unknown type, bad tier/thinking, missing role, a dispatcher-issued directive, misaddressed directive, uncorrelated report, director ack, a retired role in a schema-2 envelope). |
| `nonce` replay refusal | `fleet/channel.py` `send` | A directive cannot be replayed into the mailbox under a previously seen nonce. |
| A2A extension enforcement | `fleet/channel.py` (`log`, `follow`, `kb`, `steer`) + `fleet/terminal.py` delivery | A steer must be director-signed, addressed to the dispatcher, correlated to a directive and name a safe mailbox id; the dispatcher loop streams each run's stdout/events to `.fleet/runs/<directive>.log` (which `follow` tails live), answers `kb` from the recorded knowledge catalogue (CANNOT-ASSESS when it is missing), and delivers a queued steer to the live run (stdin + log stream + run marker) — never a re-dispatch. |
| `scripts/check-fleet-vocabulary.sh` | `make verify` (`fleet-vocabulary`) | The role vocabulary has one authority and the code agrees with it; a retired role in a schema-2 envelope is refused BY NAME; the write seam is single-emit and negotiated from the recipient's own heartbeat declaration; and a v2 emitter that still emits a v1 role is detected (provoked by mutating the seam in a scratch copy). |
| `scripts/check-fleet-contract.sh` | `make verify` (`fleet-contract`) | This contract still declares the six verbs, the four trust rules and the schema/ADR references; every declared verb maps to a message type the channel actually implements; and the check proves itself non-vacuous by mutating its own input. |
| `scripts/check-board-gate.sh` (board triggers) | `make verify` (`board-gate`) | A stale board snapshot produces exactly ONE refresh, and when freshness does not return the directive is PARKED and never re-dispatched; the gate provokes the refusal against a real scratch fleet and fails a mutant that forgets the park. |
| `scripts/check-fleet-channel.sh` | `make verify` (`fleet-channel`) | The channel refuses invalid traffic **and** the queue-liveness runaway alarm (§9) is raised by the gate-of-record itself: provoked, re-read with the excursion gone (still raised), cleared by `ack`, with a second `ack` refused. |
| `fleet/tests/test_health_alarm.py` | `make tests` (`fleet` suite) | The same alarm properties, asserted per-case: the signal's three readings, the raise, the latch surviving the excursion, and the `ack` that is the only clear. |
| `fleet/tests/test_contract.py` | `make tests` (`fleet` suite) | The same declarations, asserted in the per-suite test corpus. |

## 7. Live transport verbs — the A2A extension (issue #367)

The M26 mailbox carried *discrete* messages only (`directive`/`ack`/`result`/
`escalate`), so live debugging was poll-and-restart. Issue #367 extends the
same transport — localhost, file-based, no daemons (GR-21: "live" is
short-poll/long-poll over the `.fleet/` mailbox, not a socket service) — with
three additive capabilities. The change widens `channel.MESSAGE_TYPES`
additively (`steer`); the schema's envelope enum is versioned by the `schema`
field (§3), because `channel.py` is the machine that runs (§0), and no validation
rule that existed before
#367 was weakened.

| Verb | Mechanism | Meaning |
|---|---|---|
| `log` | `channel.py log --directive <id> --line <text>` | Append one event to a directive's live log stream (`.fleet/runs/<directive>.log`). |
| `follow` (`listen`) | `channel.py follow --directive <id>` | Tail that stream live — the `follow`/`listen --directive` view of one run's stdout/events. |
| `kb` | `channel.py kb --text <query> [--kind …]` | Query the institutional KB (`governance/knowledge/`), answered from the recorded catalogue with source-backed evidence per hit. |
| `steer` | `channel.py steer --directive <id> --body <hint>` | Queue a mid-run steering hint for an in-flight directive; the dispatcher loop delivers it to the live run. |

**The extension keeps the hierarchy and the mailbox:**

- Only the director may `steer`, and a steer names an in-flight directive of the
  dispatcher's. The principal steers by ordering the director (`task.kind: steer`),
  which relays a director-signed steer — the principal never addresses the
  dispatcher
  directly.
- A steer is delivered to the *running* child (its stdin and its live log
  stream, stamped into the run marker) and then consumed. A steer whose run is
  not live yet stays queued; one whose run already finished is dropped — a
  hint for a finished run must never steer the next run of the same directive.
- `log`/`follow`/`steer` refuse a directive id that is not a safe mailbox name;
  `kb` answers only from the recorded catalogue and is CANNOT-ASSESS when it is
  missing — never an invented answer.

### 7.1 A2A is the PRIMARY control plane — not a fallback

§7 called the live verbs an "extension". The word was wrong, and it is the word
that would let a later lane treat the channel as optional. The A2A steering
channel — the mailbox of §5 plus the live verbs of §7 — **is the fleet's
PRIMARY control plane**: the principal orders the director, the director issues
directives, and every other path to an agent is subordinate to it. "Extension"
describes the *verbs* §7 added, never the *status* of the channel that carries
them. This subsection adds no verb of its own: §2's vocabulary stays exactly
six, and no section above is renumbered.

The rules below are normative, and they are what "primary" means here:

1. **A directive is the only authorisation for work.** Work happens because the
   director issued a directive for it — an issue, a lane, a FinOps block. A chat
   message, an issue comment, a verbal hand-off and a lane's own reading of the
   board are *not* authorisations; a lane that starts work without one has
   invented its own authorisation, and the claim ledger refuses it (GR-20).
   Ordering is the same rule seen from the principal's side: to start work is to
   `order` the director, and the director is what turns the order into a directive.
2. **The dispatcher never picks work.** The director's session runs DeepSeek v4.1
   Flash with thinking effort **off** (DSv4FNone) and executes only the directives
   it is handed: it drains `.fleet/inbox`, spawns one epic-focused executor per
   directive, and reports. It never picks its own work, never re-plans the
   board, and never widens its own scope — that is the whole of its agency, and
   it is none.
3. **The à-la-carte reachability invariant.** Every agent is reachable on any
   principal or director reason, at any moment: the principal orders the director,
   and
   the director addresses whichever agent it needs (§1b). No agent is out of band,
   and none is reachable only through a side door — work is never driven
   "manually" around the channel, because a channel that can reach some agents
   but not others is not a control plane, it is a preference.
4. **The director remains the only issuer.** Only the director issues directives
   (the trust model in §4). A directive may **supersede** an earlier directive for
   the same issue by its `supersedes` field or by naming that directive's id —
   never by silent overwrite — so the order of orders is recorded in artifacts
   rather than reconstructed from memory.
5. **Any agent is replaceable, and the artifacts are the state.** An agent may
   be replaced, or have new instructions frontloaded, at any moment. All state
   that matters lives in ARTIFACTS — the issue, the branch, the claim ledger,
   the board, the directive — never only in an agent's context, so a
   replacement resumes from the artifacts alone. Rules 1–4 are what a
   replacement inherits; rule 5 is why it can inherit them at all.

**The principal's way in is documented.** Which surface reaches which rung, the
exact command for each, and what works with no shell on the box versus what
does not: [`../docs/OPERATOR-ACCESS.md`](../docs/OPERATOR-ACCESS.md).

## 8. Board triggers — refresh-or-park on a stale snapshot (issue #727)

A claim is validated against the committed board snapshot, and a snapshot older
than the declared threshold (`SNAPSHOT_STALENESS_MINUTES`, 15, in
`governance/policy/lease.py`) is refused with the `snapshot-stale` reason. That
refusal **names its own remedy** — refresh the board — and for a long time
nothing ran the remedy: the same directive came back every watch cycle, could
never be claimed, and repeated for ever. A fail-closed refusal is only half a
control.

The trigger contract is the other half, and it has three parts:

1. **One refresh, bounded.** On `snapshot-stale` the consumer performs
exactly one board refresh (`governance.dispatch.snapshot.refresh`, the same
verb `dispatch snapshot --from-github` runs), inside a bounded window
(`TRIGGER_WINDOW_SECONDS`, 60s — a `gh` that hangs must not hang the loop). A
refused network, a failing `gh` and an expired window are all first-class
outcomes, never an unhandled crash.
2. **Park, do not re-dispatch.** If the refresh does not clear the staleness the
directive is PARKED in the deferred queue (`<fleet>/parked/<directive>.json`) and
`channel watch` will not return it until freshness returns. A parked directive is
never refreshed a second time, which is what makes "exactly one refresh" true
rather than "one per cycle".
3. **Report the transition once,** naming the snapshot's `generated_at` and the
threshold it tripped, so the principal reads the board's age instead of a refusal
repeated every cycle.

**A park is not a dead letter.** The runaway guard's dead letter (issue #723)
retires an order for ever and *moves* it out of the inbox; a park keeps the
principal's live order in the inbox and records only the hold, because the work is
waiting on the board rather than on the principal. Freshness returning releases a
parked directive with **no principal action** — `channel watch` unparks it as it
drains the mailbox. The two compose: the park holds the directive and the runaway
guard still counts the attempt, so neither the park nor the attempt budget can be
bypassed.

The verbs are `governance/dispatch/cli.py trigger --directive <id>` (the
consumer's entry point, exit 0 = dispatchable, 1 = the park holds it) and
`refresh_or_park(...)` in `governance/dispatch/snapshot.py` (the library seam the
loop calls). `scripts/check-board-gate.sh` is the gate of record: it provokes the
stale case against a real scratch fleet, counts the refresh attempts, and treats
a directive that is re-dispatched after a park as a failure.

## 9. Provenance

The vocabulary and role separation in this contract are harvested, not invented
(GR-10); every source is recorded in
[`../docs/CANNIBALIZATION.md`](../docs/CANNIBALIZATION.md) §6 with repo, path
and license. The prior art is the mature fleet model in `kushin77/leaderboard`
and `kushin77/capital-underwriting`, both proprietary and owner-authored, so
what is reused is the *pattern and the vocabulary* — reimplemented here for this
repo's Python/JSON substrate — never copied code.

## 9. Runaway + queue-liveness alarm (issue #728)

The runaway that motivated the attempt cap (#723) — 49 concurrent `make verify`
runs on one box — was caught by a human *noticing* it. Detection is a signal,
and a signal has to be an ALARM: an excursion that clears itself before anyone
looks is indistinguishable from one that never happened. This section is the
contract for that alarm. It lives in `fleet/health.py` and it is emitted by the
gate of record (§6, `check-fleet-channel.sh`), not only by an ad-hoc command.

**The signal reads three things, from state the loop already writes** (it never
spawns anything):

| Reading | Source |
|---|---|
| inbox depth | the pending envelopes in `<fleet>/inbox`, in send order (`channel.ordered_by_time`) |
| oldest-directive age | the `ts` of the first pending envelope, in minutes |
| dead-letter count | `runaway.inventory` — the same read `channel.py status` makes |

**A runaway condition LATCHES.** The condition is any of: more pending
directives than the depth cap; an oldest pending directive older than the age
cap; a dead-letter count at or above the dead-letter cap. When one holds, the
alarm is RAISED and the raise is persisted at `<fleet>/health/alarm.json`
(`runtime.HEALTH` / `runtime.ALARM`). It then stays raised — through the next
measurement, and the next — until a principal acknowledges it. A later
measurement that no longer sees the condition does **not** clear it: the second
read is still raised, still naming the moment the condition began and the
directives and worktrees responsible.

**It names the culprit.** The responsible directives are the pending envelopes
(their `id`, or the mailbox stem); the responsible worktrees come from two
declared joins, never a guess — a live claim is linked to the directive it was
taken for (`ClaimEvent.directive_id`, #723) and a lane record carries the
worktree its session was provisioned into (`governance/isolation`). A directive
that names an issue no lane has claimed still resolves through the lane record
for that issue, so a queue blocked *before* the claim is taken is named too.

**`ack` is the only clear, and it is a human verb.** Acknowledging writes the
acknowledgee, the time and the note beside the original evidence (the record is
kept, not deleted: the question asked afterwards is "was this alarmed, and who
cleared it"). A second `ack` with nothing raised is refused, so the verb cannot
pass as a no-op.

**The knobs are the contract** (an unreadable value is refused, never silently
defaulted — the same asymmetry `fleet/runaway.py` declares):

| Knob | Default | Meaning |
|---|---|---|
| `AO_RUNAWAY_INBOX_DEPTH` | `24` | pending directives the inbox may hold |
| `AO_RUNAWAY_OLDEST_MINUTES` | `120` | minutes a pending directive may sit undrained |
| `AO_RUNAWAY_DEAD_LETTERS` | `5` | dead-lettered directives that mean the queue is not moving |

**The names do not travel as metrics.** Per-session identity — a directive id, a
worktree path — is refused as a label by name in `fleet/health_signals.py`
(ADR-0022 D5 / `kushin77/monitoring-stack#178`), so the alarm names the culprit
in the local signal and its latch artifact, where the principal reads it, and the
exported family carries only counts. `check` reports the latch read-only; the
`alarm` verb raises it. Neither writes outside the fleet directory it was given,
and neither writes at all when the condition is clear.

```bash
python3 fleet/health.py check   # the full signal: rungs + queue + the latch, read-only
python3 fleet/health.py alarm   # measure the queue; raise + latch; exit 2 when raised
python3 fleet/health.py ack     # acknowledge the latch — the only way it clears
```
