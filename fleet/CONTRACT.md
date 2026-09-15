# Session fleet steering contract (v1)

> **Status:** normative · **Ratified by:** issue #161 (milestone M26, epic #160)
> · **Transport decision:** [ADR-0011](../docs/decision-records/ADR-0011-session-fleet-transport.md)

This is the **normative** contract for the session fleet operating model: the
roles, the directive vocabulary, the message schema, and the trust rules. It is
enforced, not advisory. `fleet/channel.py` refuses traffic that violates it, and
[`../scripts/check-fleet-contract.sh`](../scripts/check-fleet-contract.sh) —
wired into `make verify` — fails the gate when any element declared here is
removed from this file (GR-12: a rule a gate cannot fail on is a formality).

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
the contract. It is written as an operator runbook (bootstrap steps, mailbox
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

| Role | Runtime | Authority |
|---|---|---|
| **operator** | The human, and the override terminal | Orders the brain. The top of the chain: it does not address the sister directly, because a chain the operator can skip is not a chain. |
| **brain** | Copilot advisor session, maximum DeepSeek vPro | The only directive issuer. Steers, verifies, merges. |
| **fleet brain** (the **sister** session) | DeepSeek v4.1 Flash, thinking effort **off** (DSv4FNone) | A dumb terminal. Drains the inbox, executes brain directives only, spawns epic-focused subagents per directive, reports results back. Never picks work on its own. |
| **subagent** (`subagent-<name>`) | Model + tier + thinking chosen by the brain's FinOps block | Epic-focused executor. One issue = one subagent = one lane. Cannot escalate its own tier. |

**Rung 1b — the operator orders the brain (added by this revision).** Issue #160
named the operator and the brain as separate rungs, but shipped no path between
them: the operator's only working trigger was to write into the sister's inbox,
which *is* the brain's job. The channel now carries the missing edge —
`channel.py order` (operator → brain) and `fleet/brain.py` (the brain loop that
signs and dispatches each order to the sister) — and refuses `operator → sister`
outright, because a hierarchy the transport cannot enforce is a suggestion.
Escalations flow the same way in reverse: sister/subagent → brain (a problem)
and brain → operator (a problem the brain has decided it must not resolve
alone).

The roster separates **ROLE** (what an agent is for) from **TIER** (which
transport carries it), **MODEL** (what answers) and **EFFORT** (how much
thinking it spends) — harvested from `leaderboard/lib/fleet-roster.sh`, whose
own header records why the separation exists: coupling a seat to a model made a
role's cost and capability drift silently. A subagent receives all four in its
directive; none of them is inferred by the subagent.

**FinOps block.** Every directive carries `model.tier` ∈ {`pro`, `flash`} and
`model.thinking` ∈ {`none`, `low`, `high`}, plus an optional `budget_hint`.
Anything outside those allowlists is refused, and only a new brain directive may
raise a tier — an executor that wants more model must ask, not take.

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
| `spawn-epic-agent` | `directive` | Brain orders the sister to spawn one epic-focused subagent. |
| `dispatch-issue` | `directive` | Brain names the issue (and lane) that subagent works; the directive is the claim's chain edge. |
| `model-directive` | `directive` | Brain sets the FinOps block for a subagent's run. |
| `handoff` | `directive` | Brain moves an in-flight epic from one subagent to another; the receiving subagent inherits the evidence, not the authority. |
| `halt` | `halt` | Brain stops the fleet. Only the brain may issue it. |
| `report` | `result` | The sister or a subagent answers the directive it was given. A synchronous progress acknowledgement is the same envelope as `ack`. |

## 3. Message schema v1 (JSON)

The normative schema is [`schema/message.schema.json`](schema/message.schema.json).
The fields the contract depends on:

| Field | Required | Purpose |
|---|---|---|
| `from` / `to` | yes | Roles: `brain`, `sister`, `subagent` or `subagent-<name>`. |
| `type` | yes | `directive` · `ack` · `result` · `halt`. |
| `id` | stamped | Unique message id (uuid4) stamped by the channel. |
| `ts` | stamped | ISO-8601 UTC timestamp stamped by the channel. |
| `correlation_id` | for `ack`/`result` | Binds a report to the directive that caused it — the completion trigger the brain waits on. |
| `nonce` | stamped | Anti-replay token; `send` refuses a nonce it has already seen. |
| `model` | for `directive` | The FinOps block: `tier`, `thinking`, optional `budget_hint`. |
| `task` | for work directives | `issue` (required), optional `epic`, optional `lane`. |
| `body` | no | Human- and agent-readable order text. |

## 4. Trust model

Four rules, and one consequence that is itself the rule:

1. **Only the brain may issue directives to the sister.** A directive from the
   sister is refused outright: the sister is a dumb terminal, and a dumb
   terminal that can rewrite its own orders is not a dumb terminal.
1b. **The operator orders the brain, and never the sister.** The channel
   validates the sender of every message before it moves, so this is enforced
   rather than requested:

   | Sender | May address | Refused with |
   |---|---|---|
   | `operator` | `brain` only, as a `directive` (an order) | *"the operator does not address the sister: it orders the brain, and the brain orders the sister"* |
   | `brain` | `sister` (directives, control) and `operator` (acks, results, escalations) | *"the brain takes orders only from the operator"* — a directive addressed to the brain from anywhere else |
   | `sister` | `brain` (acks, results, escalations) | *"only the brain may issue directives to the sister"* |
   | `subagent-*` | `brain` (results, escalations) | *"only the brain may issue directives to the sister"* |

2. **The sister may only spawn subagents per a directive.** Spawning is an
   execution of a brain order, never a decision of the sister's own.
3. **Subagents report back through the sister.** A subagent's result travels the
   same channel as the directive that caused it, correlated by `correlation_id`;
   it never addresses the brain as a peer authority.
4. **Everything else is refused.** Not "discouraged", not "logged" — refused by
   `channel.validate` before the message moves, so an out-of-contract message
   never reaches a mailbox.
5. **Only the brain may steer a run mid-flight (added by issue #367).** A
   `steer` is brain-signed and correlated to an in-flight directive; the sister
   loop delivers it to the live run without killing or re-dispatching it. The
   operator steers by ordering the brain, which relays the brain-signed steer
   (`task.kind: steer`) — the chain is never skipped.

Consequences of the model, stated so they are not discovered later:

- The brain cannot `ack` or `result` its own directives — a report must come
  from the party that executed.
- Only the brain may `halt`.
- A subagent cannot escalate its own tier or thinking effort; only a
  `model-directive` from the brain changes them.

## 5. Transport decision

The transport is the **file mailbox** (`fleet/channel.py` over
`.fleet/{inbox,sent,outbox,done}`), with the **push → wait → completion trigger**
loop: the brain sends a directive, blocks on `wait`, and wakes when the executor
`report`s. The alternatives considered — the hub's A2A product layer, and the
engine's state machine — and the reasons the mailbox is the transport of record
*now* (with A2A as the graduation target once this repo's M9 ships) are recorded
in [ADR-0011](../docs/decision-records/ADR-0011-session-fleet-transport.md).
The channel references that record in its own module docstring, so the transport
and its rationale cannot drift apart.

## 6. Enforcement

| Control | Where | What it proves |
|---|---|---|
| `channel.validate` | `fleet/channel.py` | Refuses out-of-contract traffic at the boundary (unknown type, bad tier/thinking, missing role, sister-issued directive, misaddressed directive, uncorrelated report, brain ack). |
| `nonce` replay refusal | `fleet/channel.py` `send` | A directive cannot be replayed into the mailbox under a previously seen nonce. |
| A2A extension enforcement | `fleet/channel.py` (`log`, `follow`, `kb`, `steer`) + `fleet/terminal.py` delivery | A steer must be brain-signed, addressed to the sister, correlated to a directive and name a safe mailbox id; the sister loop streams each run's stdout/events to `.fleet/runs/<directive>.log` (which `follow` tails live), answers `kb` from the recorded knowledge catalogue (CANNOT-ASSESS when it is missing), and delivers a queued steer to the live run (stdin + log stream + run marker) — never a re-dispatch. |
| `scripts/check-fleet-contract.sh` | `make verify` (`fleet-contract`) | This contract still declares the six verbs, the four trust rules and the schema/ADR references; every declared verb maps to a message type the channel actually implements; and the check proves itself non-vacuous by mutating its own input. |
| `scripts/check-board-gate.sh` (board triggers) | `make verify` (`board-gate`) | A stale board snapshot produces exactly ONE refresh, and when freshness does not return the directive is PARKED and never re-dispatched; the gate provokes the refusal against a real scratch fleet and fails a mutant that forgets the park. |
| `fleet/tests/test_contract.py` | `make tests` (`fleet` suite) | The same declarations, asserted in the per-suite test corpus. |

## 7. Live transport verbs — the A2A extension (issue #367)

The M26 mailbox carried *discrete* messages only (`directive`/`ack`/`result`/
`escalate`), so live debugging was poll-and-restart. Issue #367 extends the
same transport — localhost, file-based, no daemons (GR-21: "live" is
short-poll/long-poll over the `.fleet/` mailbox, not a socket service) — with
three additive capabilities. The change widens `channel.MESSAGE_TYPES`
additively (`steer`); the schema's envelope enum stays v1, because `channel.py`
is the machine that runs (§0), and no validation rule that existed before
#367 was weakened.

| Verb | Mechanism | Meaning |
|---|---|---|
| `log` | `channel.py log --directive <id> --line <text>` | Append one event to a directive's live log stream (`.fleet/runs/<directive>.log`). |
| `follow` (`listen`) | `channel.py follow --directive <id>` | Tail that stream live — the `follow`/`listen --directive` view of one run's stdout/events. |
| `kb` | `channel.py kb --text <query> [--kind …]` | Query the institutional KB (`governance/knowledge/`), answered from the recorded catalogue with source-backed evidence per hit. |
| `steer` | `channel.py steer --directive <id> --body <hint>` | Queue a mid-run steering hint for an in-flight directive; the sister loop delivers it to the live run. |

**The extension keeps the hierarchy and the mailbox:**

- Only the brain may `steer`, and a steer names an in-flight directive of the
  sister's. The operator steers by ordering the brain (`task.kind: steer`),
  which relays a brain-signed steer — the operator never addresses the sister
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
PRIMARY control plane**: the operator orders the brain, the brain issues
directives, and every other path to an agent is subordinate to it. "Extension"
describes the *verbs* §7 added, never the *status* of the channel that carries
them. This subsection adds no verb of its own: §2's vocabulary stays exactly
six, and no section above is renumbered.

The rules below are normative, and they are what "primary" means here:

1. **A directive is the only authorisation for work.** Work happens because the
   brain issued a directive for it — an issue, a lane, a FinOps block. A chat
   message, an issue comment, a verbal hand-off and a lane's own reading of the
   board are *not* authorisations; a lane that starts work without one has
   invented its own authorisation, and the claim ledger refuses it (GR-20).
   Ordering is the same rule seen from the operator's side: to start work is to
   `order` the brain, and the brain is what turns the order into a directive.
2. **The sister is a dumb terminal.** The fleet brain runs DeepSeek v4.1 Flash
   with thinking effort **off** (DSv4FNone) and executes only the directives it
   is handed: it drains `.fleet/inbox`, spawns epic-focused subagents per
   directive, and reports. It never picks its own work, never re-plans the
   board, and never widens its own scope — that is the whole of its agency, and
   it is none.
3. **The à-la-carte reachability invariant.** Every agent is reachable on any
   operator or brain reason, at any moment: the operator orders the brain, and
   the brain addresses whichever agent it needs (§1b). No agent is out of band,
   and none is reachable only through a side door — work is never driven
   "manually" around the channel, because a channel that can reach some agents
   but not others is not a control plane, it is a preference.
4. **The brain remains the only issuer.** Only the brain issues directives (the
   trust model in §4). A directive may **supersede** an earlier directive for
   the same issue by its `supersedes` field or by naming that directive's id —
   never by silent overwrite — so the order of orders is recorded in artifacts
   rather than reconstructed from memory.
5. **Any agent is replaceable, and the artifacts are the state.** An agent may
   be replaced, or have new instructions frontloaded, at any moment. All state
   that matters lives in ARTIFACTS — the issue, the branch, the claim ledger,
   the board, the directive — never only in an agent's context, so a
   replacement resumes from the artifacts alone. Rules 1–4 are what a
   replacement inherits; rule 5 is why it can inherit them at all.

**The operator's way in is documented.** Which surface reaches which rung, the
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
threshold it tripped, so the operator reads the board's age instead of a refusal
repeated every cycle.

**A park is not a dead letter.** The runaway guard's dead letter (issue #723)
retires an order for ever and *moves* it out of the inbox; a park keeps the
operator's live order in the inbox and records only the hold, because the work is
waiting on the board rather than on the operator. Freshness returning releases a
parked directive with **no operator action** — `channel watch` unparks it as it
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
