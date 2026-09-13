# fleet — the session fleet operating model (M26, issue #160)

This repo is the complete end-to-end IT department for any organization
(Enterprise/SaaS): work intake, dispatch, execution, verification, merge,
observability, FinOps and governance, run by agents. This directory is the
session layer that operates it.

## The model

| Role | Runtime | Behaviour |
|---|---|---|
| **Brain** | Copilot session (advisor, max DeepSeek vPro) | Issues directives. The only directive issuer. Steers and verifies. |
| **Fleet brain (sister)** | DeepSeek v4.1 Flash, **no thinking** (DSv4FNone) | A dumb terminal: drains `.fleet/inbox`, executes only brain directives, spawns epic-focused subagents per directive, writes acks/results to `.fleet/outbox`. Never picks issues on its own. |
| **Fleet subagents** | DeepSeek agent model, tier/thinking chosen by the brain's FinOps block | Epic-focused executors. One issue = one subagent = one lane. |

The brain ↔ sister and sister ↔ subagent traffic uses this file-mailbox
channel as the transport of record (localhost mechanics, GR-21). The message
contract is `schema/message.schema.json`; the channel CLI enforces it. The
hub's A2A product layer (this repo's M9, #101–#109) is the platform contract
this model will graduate onto when it ships.

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
* Only the brain may address a directive to the sister; the sister can never
  issue directives (dumb-terminal rule, enforced).
* `model.tier` ∈ {`pro`, `flash`} and `model.thinking` ∈ {`none`, `low`,
  `high`}; anything else is refused. A subagent cannot raise its own tier —
  only a new brain directive may escalate.

## Mailbox

```
.fleet/inbox/    pending directives for the sister   (runtime, gitignored)
.fleet/sent/     the brain's copy of what it sent    (runtime, gitignored)
.fleet/outbox/   acks and results back to the brain  (runtime, gitignored)
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
