---
description: "CMR prompt-evolution role prompt. Goal-first. Diagnose an AI failure in a governed repo, classify the root cause, fix the right surface of the instruction layer, and prove the fix with a before/after check. Fill {{...}} fields, then execute."
harvested_from:
  - "kushin77/vscode-memory@.github/prompts/evolve-memory-system.prompt.md"
  - "kushin77/vscode-memory@.github/prompts/fix-copilot-weakness.prompt.md"
  - "kushin77/intelligence@prompt_tuner.py"
---

# ROLE: PROMPT-EVOLUTION — {{failure_id}}

## Connections

- **Owner-lane:** qa-sme
- **Class:** class
- **Connects-to:** consumes=none; called-by=none; gates=none
- **Env:** none
- **Updated-by:** qa-sme (2026-09-12)
- **Landed-by:** 99f4eec

## Goal
Turn one concrete AI failure into a permanent instruction-layer improvement.
Success = the same question now gets the right answer, the fix is scoped to the
surface that actually failed, and the change is recorded with provenance.

## Step 1 — Describe the failure
State exactly: the wrong output, the expected output, and the file/task
involved. No abstract gripes — one reproducible example.

## Step 2 — Classify the root cause

| Category | Indicator | Fix surface (CMR) |
|---|---|---|
| Stale context | Cited an outdated API/rule/path | Re-read `AGENTS.md` → `docs/`; fix the stale doc, not the reader |
| Missing instruction | Violated a convention with no written rule | New `guardrails/instructions/<scope>.md` |
| Wrong scope | Instruction exists but never activated | Fix the `applyTo` glob (verify against real paths) |
| Wrong path | Used `fast` where `deep`/`strict` applied | `route-by-risk.agent.md`; record the misroute |
| Missing capability | Needed a prompt/agent that doesn't exist | New `guardrails/prompts|agents/` asset + provenance (GR-10) |
| Bad prompt tuning | Right prompt, wrong parameters | Tune temperature/top_k/top_p per Step 3, A/B it |
| Hallucination | Invented a file or API | Grounding rule: read before citing; add negative example |

## Step 3 — Fix the right surface (one change, one validation)

- **Instruction file** — concrete rule ("Never do X. Always do Y because Z.")
  + one positive and one negative example; frontmatter `description` +
  `applyTo` that matches the failing path.
- **Prompt** — use `guardrails/prompts/*` style: goal-first, constraints,
  steps, verify. Record `harvested_from` if adapted from a source repo (GR-10).
- **Parameter tuning** — vary one parameter at a time (e.g., temperature
  high→low for determinism, top_k for sampling breadth); A/B the baseline vs
  the variant on the same failing input and keep the winner with evidence.

## Step 4 — Validate the fix
- Re-ask the exact question that failed; confirm the correct output now.
- If a guardrail changed, run `make verify` (paste output).
- If not better: the root-cause class was wrong — reclassify, don't stack fixes.

## Step 5 — Record the lesson
```
## [YYYY-MM-DD] Weakness: <one line>
- Root cause: <category from Step 2>
- Fix applied: <file changed + why>
- Validated: <before/after evidence>
```
Land it in the right scope: `/memories/` (persistent, cross-workspace),
`/memories/repo/` (this repo), `controller/lessons.md` (ecosystem-wide), or
`canibalization/INDEX.md` (if an asset was harvested).

## What not to do
- Don't retrain/repair the wrong surface — check if it's a missing instruction
  vs stale doc vs wrong route before touching anything.
- Don't add an instruction whose `applyTo` glob doesn't match real paths.
- Don't fix the single answer and leave the system broken — the goal is the
  system, not the reply.

No-questions doctrine: apply `docs/DEFAULTS.md`; escalate only per GR-22 (secrets / apply / merge / irreversible).
