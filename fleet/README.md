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

## The model

| Role | Runtime | Behaviour |
|---|---|---|
| **Brain** | Copilot session (advisor, max DeepSeek vPro) | Issues directives. The only directive issuer. Steers and verifies. |
| **Fleet brain (sister)** | DeepSeek v4.1 Flash, **no thinking** (DSv4FNone) | A dumb terminal: drains `.fleet/inbox`, executes only brain directives, spawns epic-focused subagents per directive, writes acks/results to `.fleet/outbox`. Never picks issues on its own. |
| **Fleet subagents** | DeepSeek agent model, tier/thinking chosen by the brain's FinOps block | Epic-focused executors. One issue = one subagent = one lane. |

The brain ↔ sister and sister ↔ subagent traffic uses this file-mailbox
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

1. Open the **sister session** in VS Code.
2. In that session, set the model to **DeepSeek v4.1 Flash** and thinking
   effort to **none** (DSv4FNone). Nothing else is needed from the operator.
3. The sister session reads `fleet/directive.json` (the standing directive)
   and starts draining `.fleet/inbox` — its dispatcher is
   `fleet/channel.py` plus the executor from issue #163.

Everything after step 2 is code: the brain sends directives with
`python3 fleet/channel.py send --message <file>`, the sister acknowledges into
`.fleet/outbox`, subagents work the issue (subject to the claim rules in
`governance/dispatch/`), and evidence returns the same way.

## Directives

```json
{
  "from": "brain",
  "to": "sister",
  "type": "directive",
  "model": { "tier": "flash", "thinking": "none" },
  "task": { "issue": 162, "epic": 160, "lane": "fleet" },
  "body": "spawn one subagent for issue #162 and report back"
}
```

* Only `directive`, `ack`, `result` and `halt` message types exist.
* A directive is also a **chain edge**: the subagent authorizes its claim with
  it — `python3 governance/dispatch/cli.py claim --issue N --agent X --lane L
  --directive <id>` — so the brain can legally direct off-frontier work while
  unordered scavenging stays refused.
* Only the brain may address a directive to the sister; the sister can never
  issue directives (dumb-terminal rule, enforced).
* Every directive is stamped with a `nonce` when it does not carry one, and
  `send` refuses a nonce it has already seen — a replay never overwrites a
  queued order (contract §3).
* `model.tier` ∈ {`pro`, `flash`} and `model.thinking` ∈ {`none`, `low`,
  `high`}; anything else is refused. A subagent cannot raise its own tier —
  only a new brain directive may escalate.

## Push → wait → completion trigger

The brain never spins a session on a task. It pushes, blocks, and wakes:

```bash
# brain
python3 fleet/channel.py send --message /tmp/directive.json      # prints the message id
python3 fleet/channel.py wait --id <id> --timeout-seconds 600    # blocks

# executor (sister / subagent), the moment the task is done:
python3 fleet/channel.py report --from sister --correlation <id> --type result --body "merged #171"

# the waiting brain prints the result and continues — that is the A2A trigger
```

`wait` matches the outbox by message id **or** `correlation_id`, so an executor
answers the exact directive that is being waited on. A timeout exits 1 (NOT-OK)
— a silent pass would be a false green. The sister dispatcher (#163) calls
`report` on completion; ack/result messages must carry their `correlation_id`
and may never come from the brain itself (enforced by the contract and the
gate).

## Sister listener loop (the pulse)

The sister has no agency of its own — it runs the listener and acts on whatever
it prints:

```bash
while true; do
  python3 fleet/channel.py watch --timeout-seconds 600   # blocks until an order arrives
  # ...execute exactly what it printed...
  python3 fleet/channel.py report --from sister --correlation <id> --type result --body "<evidence>"
done
```

`watch` exits 1 (IDLE) when nothing arrives inside the window — that is a signal
to run it again, not an error. Reporting **consumes** the directive: it leaves
`.fleet/inbox` and lands in `.fleet/done`, so the pending count is always the
number of outstanding orders. Without a running listener, queued directives sit
unread — which is exactly what a timed-out `wait` on the brain side means.

## Escalation + the live log (both sides idle, never asleep)

The brain and sister never sleep — they idle and wait to be pinged:

```bash
# sister/subagent: when a directive hits trouble, raise it instead of going dark
python3 fleet/channel.py escalate --from sister --correlation <id> \
  --severity critical --body "verify failed twice: <exact error>"

# brain: idle-watch the whole channel as a live log
python3 fleet/channel.py listen --timeout-seconds 0
```

`listen` tails `.fleet/slog.jsonl` (the append-only structured log every
`send`/`report`/`escalate` writes) and prints each message the moment it lands,
so an escalation from the sister pings the brain terminal in real time. The
brain stays blocked on `listen` — idle, not asleep — and answers on demand.
Escalations must carry `correlation_id` and a `severity` (`info`/`warn`/
`critical`) and may only come from the sister or a subagent, never the brain
(enforced by the gate).

## Mailbox

```
.fleet/inbox/    pending directives for the sister   (runtime, gitignored)
.fleet/sent/     the brain's copy of what it sent    (runtime, gitignored)
.fleet/outbox/   acks and results back to the brain  (runtime, gitignored)
.fleet/done/     directives answered and consumed   (runtime, gitignored)
```

`fleet/directive.json` and the schema are tracked artifacts — the sister can
always discover its standing orders from a clean clone.

## The gate

```bash
bash scripts/check-fleet-channel.sh   # 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS
```

The check validates `fleet/directive.json` against the contract and runs the
channel's own mutants (unknown message type, bad tier, bad thinking, missing
role, a sister-issued directive) — each mutant must be refused, so the check
cannot pass vacuously.
