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
| **Operator** | You, at the override terminal | Orders the **brain** (`channel.py order`). Never addresses the sister directly — the channel refuses it, because skipping a rung makes the brain advisory. |
| **Brain** | DSv4PM (DeepSeek v4 Pro Max) with **human override** | The middle rung and the only directive issuer: `fleet/brain.py` drains the operator's orders, signs each one into a directive for the sister, and reports back. |
| **Fleet brain (sister)** | DeepSeek v4.1 Flash, **no thinking** (DSv4FNone) | A dumb terminal: drains `.fleet/inbox`, executes only brain directives, spawns epic-focused subagents per directive, writes acks/results to `.fleet/outbox`. Never picks issues on its own. |
| **Fleet subagents** | DeepSeek agent model, tier/thinking chosen by the brain's FinOps block | Epic-focused executors. One issue = one subagent = one lane, each in its own worktree. |

The chain is enforced by the transport, not by convention:

```
operator ──order──▶ brain ──directive──▶ sister ──spawn──▶ subagent
   ▲                   ▲                     │                 │
   └──── ack/report ────┴───── ack/report ────┴──── result ────┘
```

```bash
# order the brain (the top of the chain, and the ONLY way in)
python3 fleet/channel.py order --message '{"type":"directive","task":{"issue":166,"lane":"session-fleet"},"body":"dispatch one subagent"}'
python3 fleet/channel.py brain-outbox           # the brain's acks and refusals
python3 fleet/channel.py brain-inbox --timeout-seconds 5   # what the brain is working on
```

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
`python3 fleet/channel.py send --message <file-or-inline-json>`, the sister acknowledges into
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

# `--message` also takes inline JSON, so a live terminal needs no temp file:
python3 fleet/channel.py send --message '{"from":"brain","to":"sister","type":"directive","correlation_id":"x","task":{"issue":5}}'

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

## Control plane (refresh / update / poke / halt / debug / watch / health)

From the brain/human terminal — without stopping the sister loop:

```bash
python3 fleet/control.py refresh    # git pull --ff-only + snapshot + make verify
python3 fleet/control.py update     # refresh + rebuild the knowledge index
python3 fleet/control.py poke       # ping the sister: it acks (liveness)
python3 fleet/control.py halt       # stop the sister loop cleanly
python3 fleet/control.py debug      # channel + board + slog tail + loop process
python3 fleet/control.py watch      # idle-watch the slog (same as listen)
python3 fleet/control.py health     # tri-state signal: 0 healthy / 1 degraded / 2 failing
```

`health` (issue #163) is read-only: it never spawns a subagent or touches the
mailbox. It reports `2 failing` when `fleet/terminal.py` is not running at
all, `1 degraded` when the loop runs but `.fleet/slog.jsonl` has gone stale or
a claim is wedged past the staleness window, `0 healthy` otherwise. Run it
directly for the raw JSON and exit code: `python3 fleet/health.py check
[--stale-minutes N]`.

`refresh` on the sister side is a **self-update**: the loop pulls, runs
`make verify`, then re-executes itself with the new code — so the terminal can
be upgraded live without a human restart. `poke` forces it to react so you can
debug it without stopping it.

## Launch the two sessions

One command opens both — the brain terminal and the never-idle sister loop:

```bash
bash fleet/run-fleet.sh        # tmux: one window, two panes (or konsole/fallback)
```

Or separately:

```bash
bash fleet/brain.sh            # brain: advisor context + idle-watch the slog
bash fleet/terminal.sh         # sister: never-idle loop (watch -> run -> report/escalate)
```

The sister loop (`fleet/terminal.py`) is code-native: it watches `.fleet/inbox`
forever, runs one subagent per directive with the agent CLI (`--runner "claude
-p"` by default; `--dry-run` prints the command), reports the result, and
escalates any failure. An empty inbox is just another poll cycle — it never
idles out. `FLEET_RUNNER` overrides the runner.

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
