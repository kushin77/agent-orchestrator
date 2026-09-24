---
description: "Use when: dispatching a subagent or choosing a model tier, deciding whether to escalate from the cheap default, sizing a task to haiku vs sonnet vs opus, or writing a dispatch brief. Renders CMR's model-tier discipline (cheap-by-default, security/IaC floor, named escalation signals, one-escalation rule)."
applyTo: "**"
---

# Model tier discipline — CMR

> Copilot-discoverable rendering of `guardrails/instructions/model-tier-discipline.md`
> (canonical). The LOW/MED/HIGH/MAX ladder and the flash/pro (haiku/sonnet+opus)
> mapping are owned by `docs/MODEL-PROFILES.md` — do not restate them from memory.

## 1. The floor comes first

Security, secrets, auth, and production-IaC work starts at **sonnet (HIGH)** and never
drops below it — regardless of how mechanical the task looks and regardless of the cheap
default below (`docs/MODEL-PROFILES.md` rule 5). The default may not override the floor.

## 2. The default

**Every dispatch starts at the cheapest tier that can finish the job** — haiku/flash by
default. The dispatcher's job is to justify *leaving* the default, never to justify
staying on it. In this repo `CLAUDE_CODE_SUBAGENT_MODEL=haiku` (project
`.claude/settings.json`) makes that the model a dispatch with no `model` field receives.

## 3. The escalation signals (name one, in the dispatch)

1. Multi-file coordination across lanes.
2. Non-trivial debugging — root cause not yet known.
3. Judgment calls / several constraints held at once.
4. The security/IaC floor — structural, needs no observed difficulty.
5. Observed failure — the cheap-tier attempt failed, looped, or could not hold the
   constraints.

**opus (L2/MAX)** only for genuinely hard architecture tradeoffs, adversarial
verification, or ambiguous specs — decision-only, time-boxed, and only after L1 was tried
or is manifestly insufficient.

## 4. The one-escalation rule

A second failure is not a third escalation — it is a signal to re-scope the lane or file a
blocker. Do not chase rising cost with rising tier.

## 5. Brief completeness is a precondition

"This is too hard for the cheap tier" is admissible only when the brief met the
complete-brief checklist (goal frontloaded, exact file set, exact verify command, worked
expected diff, explicit constraints/non-goals, context pack) — canonical template:
`guardrails/prompts/dispatch.prompt.md`. Fix the brief and re-dispatch cheap first.

## 6. State the reason

Every non-default dispatch states tier + model + a one-line reason
(`docs/MODEL-PROFILES.md` rule 3). An unexplained above-floor dispatch is a hygiene
breach the cost report treats as a failure condition.
