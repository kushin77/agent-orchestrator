---
description: "Use when: a task is ambiguous, a default is unclear, you are tempted to stop and ask the operator for clarification or approval, or you must decide whether an escalation is warranted. Renders CMR's GR-22 no-questions doctrine (apply docs/DEFAULTS.md and proceed; escalate only the HARD list)."
applyTo: "**"
---

# No-questions doctrine — CMR (GR-22)

> Copilot-discoverable rendering of `guardrails/instructions/no-questions.md`
> (canonical; enforced by `scripts/check-no-questions.sh`, wired into `make verify`).
> Defaults registry: `docs/DEFAULTS.md`. Rule statement: `GOLDEN-RULES.md` GR-22.

## The rule

Apply the documented defaults and proceed autonomously — IaC/code-native,
no-human-needed. Stopping to ask for clarification is a failure to consult
`docs/DEFAULTS.md`, not a property of the task. A default that is missing is a board bug
to file (GR-20), not an escalation.

## The HARD escalation list (the only reasons to stop)

1. **Secrets / tokens** — a real secret is required and none is reachable via env / GSM.
2. **`terraform apply`** — infra is gated; the sanctioned path is
   `ops/run.sh gate_apply --confirm` (`controller/gate-apply.sh`), never ad-hoc.
3. **Irreversible data deletion** — destructive, no documented default, no rollback anchor.
4. **A genuinely irreversible choice with no documented default.**

Merge/close is **not** on this list for this repo: see the merge/close posture in
`agent-operating-doctrine.instructions.md` (solo-owner model — merge is execution once
verification passes). Even on the list above: if the action is **reversible**, record the
assumption in the issue and continue rather than stopping.

## What to do instead

1. Look the situation up in `docs/DEFAULTS.md` and apply the default exactly.
2. If no default exists, file the gap on the board (GR-20) and proceed with the closest
   documented default.
3. When a choice is ambiguous but reversible, record the assumption in the issue and go.

## Never

- Invent a default that contradicts a golden rule.
- File a question as an issue when a default already exists — the default is the answer.

**Known divergence:** the canonical file's HARD list still contains "Merge / approve" as
an ask-first item; the solo-owner posture in `.github/copilot-instructions.md` supersedes
it here and the canonical list is tracked as a parity gap.
