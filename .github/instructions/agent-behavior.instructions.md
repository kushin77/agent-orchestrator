---
description: "Use when: dispatching subagents, planning a parallel work wave or lane set, deciding fan-out breadth, or picking up the next task after a wave finishes. Renders CMR's standing agent conduct contract (subagent-first max-parallel, ASAP-never-idle, minimal operator questions, board-first, close with evidence)."
applyTo: "**"
---

# Agent behavior — CMR

> Copilot-discoverable rendering of `guardrails/instructions/agent-behavior.md`
> (the canonical, model-agnostic behavior layer over `AGENTS.md` →
> `docs/DEFAULTS.md` → `docs/EXECUTION-PLAN.md` lanes → `fleet/DISPATCH.md`).
> Read the canonical file when a case is subtle; this card routes, it does not restate.

1. **Subagent-first, max parallel.** Delegate execution; the main thread orchestrates,
   verifies, and commits. Fan out the **maximum disjoint ready set** in one wave — one
   subagent per lane, one message = many parallel calls. No two lanes in a wave share a
   file (`fleet/MANIFEST.tsv` `owns`).
2. **ASAP — never idle.** When a wave finishes, re-check the board and dispatch the next
   ready set immediately; no pause between waves. Stand down only at 0 open issues +
   PRs + queue.
3. **Minimal operator questions (GR-22).** Apply `docs/DEFAULTS.md` and proceed; a
   missing default is a board bug (GR-20), never a reason to pause. Escalate only the
   HARD list in `guardrails/instructions/no-questions.md`.
4. **Board mandate (GR-20).** Every task — planned or ad-hoc — is a board issue before
   work; off-board work is backfilled.
5. **Evidence, not claims (GR-12).** "Done" = the issue's `Verify:` command **and** the
   repo gate both pass, with the exact command and real output reported.

Per-dispatch tier selection: `guardrails/instructions/model-tier-discipline.md`.
Lead-role, lane/branch, and denial rules: `agent-operating-doctrine.instructions.md`.
