# `governance/finops` — FinOps model chooser (M26, issue #164)

The brain issues a directive; the directive carries a FinOps block
(`model.tier` + `model.thinking`); this module converts that block into the
**spawn record** the fleet actually runs — and refuses everything that is not
the brain's choice.

> The tier is the brain's decision, not the agent's. A subagent that asks for a
> different tier is refused, in both directions, because a tier changes only in
> a **new brain directive**.

## Vocabulary (harvested — see §Provenance)

<!-- finops-vocabulary:start -->
tiers: flash, pro, auditor
thinking: none, low, medium, high
<!-- finops-vocabulary:end -->

The block between the markers above is not decoration:
`scripts/check-finops-chooser.sh` compares it, line for line, against
[`policy.json`](policy.json), and against the enum in
`fleet/schema/message.schema.json` and the constants in `fleet/channel.py`. Change
a name in one place and the gate fails.

| Name | Where it came from | Meaning here |
|---|---|---|
| `flash` | `tier-policy.json` → `tiers.flash` (`deepseek-v4-flash`) | the volume tier — cheapest capable model |
| `pro` | `tier-policy.json` → `tiers.pro` (`deepseek-v4-pro`) | the reasoning tier |
| `auditor` | `tier-policy.json` → `tiers.auditor` (`deepseek-v4-pro`) | the verification tier; `route-policy.json` maps its `strict` route here |
| `none` | the fleet's thinking-off state (`LB_THINKING_DEEPSEEK=disabled`) | thinking disabled; the sister's seat (DSv4FNone) |
| `low` / `medium` / `high` | `role_effort()` in `lib/fleet-roster.sh` | thinking effort, exactly as the mature roster spells it |

`none` is not a coinage of this repo: it is the state the fleet switches to with
`LB_THINKING_DEEPSEEK=disabled`, and it is already the standing directive's value
in [`fleet/directive.json`](../../fleet/directive.json).

## Allowlist

| Role | Tiers | Thinking |
|---|---|---|
| `sister` | `flash` | `none` |
| `subagent-<name>` | `flash`, `pro`, `auditor` | `none`, `low`, `medium`, `high` |

Both tables live in [`policy.json`](policy.json); nothing is hardcoded in code.

## Refusals

Every refusal exits **1** and names exactly one finding:

| Finding | When |
|---|---|
| `FINOPS-BAD-DIRECTIVE` | the input is not a `directive` message |
| `FINOPS-MISSING-MODEL-BLOCK` | the directive carries no `model` block to enforce |
| `FINOPS-UNKNOWN-TIER` | the tier (given or requested) is outside the harvested three |
| `FINOPS-UNKNOWN-THINKING` | the thinking level (given or requested) is outside the harvested four |
| `FINOPS-ROLE-NOT-ALLOWED` | the directive puts a role at a tier/effort its allowlist forbids |
| `FINOPS-SELF-ESCALATION` | an agent requested a tier/effort that differs from the directive |
| `FINOPS-SPAWN-MALFORMED` | a spawn record is not an object or is missing fields |
| `FINOPS-SPAWN-TAMPERED` | a spawn does not reproduce the brain's choice (tier, effort, model, fingerprint, or author) |

A missing/unparseable policy or input exits **2** (`CANNOT-ASSESS`) — it is never
read as a pass.

## CLI

```bash
# the harvested vocabulary (the gate compares this to the policy, the schema and the channel)
python3 governance/finops/chooser.py vocabulary

# the brain's standing directive for the sister -> the DSv4FNone seat
python3 governance/finops/chooser.py choose --directive fleet/directive.json --role sister

# a subagent that wants a better tier than the directive gave it -> refused, rc 1
python3 governance/finops/chooser.py choose --directive some-directive.json \
  --role subagent-a1 --request-tier auditor

# re-verify a recorded spawn against the directive that authorised it
python3 governance/finops/chooser.py verify-spawn --directive some-directive.json --spawn spawn.json
```

## Dispatcher integration (#163)

`fleet/dispatcher*` (issue #163) owns the spawn loop. It calls
`choose(policy, directive, role, requested_tier=..., requested_thinking=...)`
**before** spawning and writes the returned
[`SpawnRecord`](chooser.py) next to the run; the subagent's answer is the only
place a `requested_*` value may come from, and it can only ever cause a refusal.
This package deliberately owns no mailbox or spawn code, so the two lanes cannot
collide on a file.

## Provenance (GR-10)

Harvested **2026-09-13** from read-only local checkouts of `kushin77/leaderboard`
and `kushin77/capital-underwriting`; both are proprietary, owner-authored repos.
Only the **pattern and the vocabulary** were reused — no source file was copied,
so no `harvested_from` code marker applies. The per-path record, with repo, path
and license, is in [`docs/CANNIBALIZATION.md`](../../docs/CANNIBALIZATION.md) §7
and mirrored machine-readably in [`policy.json`](policy.json) `sources`.

The gate that enforces all of the above is
[`scripts/check-finops-chooser.sh`](../../scripts/check-finops-chooser.sh);
the test suite is [`tests/`](tests/test_chooser.py).
