# Global operating instructions (all projects)

## Subagent-first execution

Delegate essentially all work to subagents via the Agent tool, not inline — small edits and single questions too.

- **Fork** (`subagent_type: "fork"`) when the work needs this conversation's context but the tool noise isn't worth keeping.
- **Fresh subagent** for self-contained work with no prior context — brief it like a new hire.
- Skip delegation only for a single trivial tool call that IS the answer; even then prefer a fork if one's already natural.
- Launch independent subagents in parallel (one message, multiple Agent calls).

## FinOps: model selection for subagents

Default to the cheapest model that can plausibly do the job; escalate only on observed difficulty, never pre-emptively. Ladder (full table: `docs/MODEL-PROFILES.md` in `shared-frontend`):

- **L0 haiku/DeepSeek (default):** lookups, mechanical edits, verification runs, simple research, routine drafting — narrow well-defined answers.
- **L1 sonnet:** multi-file implementation, non-trivial debugging, judgment-call review, multiple held constraints. Security/secrets/auth/IaC never drops below L1.
- **L2 opus:** genuinely hard reasoning (architecture tradeoffs, adversarial verification, ambiguous specs) only after L1 is tried or clearly insufficient.

State the tier and why in one line whenever it's not L0.

### SME profiles

Dispatch via a named SME when one fits (`~/.copilot/agents/`; spec in `docs/SME-PROFILES.md`): `frontend-sme`, `security-sme`, `iac-sme`, `sync-sme`, `qa-sme`, `architecture-sme` (L2, decisions only), `platform-sme`. Each card names its tier, lanes, guardrails. Quality tags against `docs/SOLUTION-CLASSES.md` (template→class→pattern→enterprise→faang→elite, IaC cross-cutting). Lanes: `docs/EXECUTION-PLAN.md` — one issue = one subagent = one lane, no shared files.

## Caveman + loop + advisor compatibility

Caveman compresses *user-facing text only* — never:
- `/loop`: keep scheduling/following its own instructions; only displayed text compresses.
- `advisor()`: call at its own specified points regardless of caveman level; weigh advice fully — brevity applies to reporting the outcome, not to seeking/weighing it.
- Code, commit messages, PR descriptions, security explanations stay normal prose (caveman's own stated boundary).

## `/focus` prompt construction

Frontload the goal in any prompt handed to a subagent/skill: objective + success criteria in the first 1-2 sentences, before background. Order: (1) goal, (2) constraints/non-negotiables, (3) context/what's tried, (4) specific steps/preferences. Lets a subagent (or a post-compaction future self) act correctly from sentence 1.
