# Contributing to agent-orchestrator

> New here? Read [`AGENTS.md`](AGENTS.md) first — it is the canonical,
> model-agnostic instruction file for AI agents and humans. This file is the
> human contributor's working guide. The GitHub issues board is the canonical
> roadmap (EPIC-00 = issue #4).

`agent-orchestrator` is the **AI-agent-orchestration service control plane**:
a multi-tenant SaaS control plane that organizes, governs, and manages
commercial AI agents (Claude, DeepSeek, Copilot, Gemini, local Ollama).
Start here:

1. **`AGENTS.md`** — how agents work in this repo (canonical, read first).
2. **`docs/ARCHITECTURE.md`** — five-pillar architecture (source of truth).
3. **`docs/EXECUTION-PLAN.md`** — lane ownership and phase/wave sequencing.
4. **`docs/GOVERNANCE.md`** — branch, provenance, and session-label
   conventions.

## Branching

- `master` is **protected by convention** — no direct pushes, no force-push.
  Branch protection as code ships with issue #6.
- All work happens on a short-lived topic branch named for the issue:
  `issue-<n>-<slug>` (e.g. `issue-5-repo-foundation`).
- One branch per issue; merged (squash) and deleted when the PR lands.

## Workflow

1. **Pick an issue.** Prefer issues that carry a `Verify:` command. State the
   goal and success criteria before acting.
2. **Stay in your lane.** Edit only files your issue owns
   (`docs/EXECUTION-PLAN.md`). Smallest focused diff; no unfinished markers,
   no debug leftovers, no unused imports.
3. **Make the change**, then run the issue's `Verify:` command **and** the
   repo gate:
   ```bash
   make verify   # gate of record: shell syntax + YAML + JSON + docs + secrets
   ```
   Plus any issue-specific command (`bash -n <script>`,
   `python3 -m json.tool <file>.json`, …).
4. **Declare AI assistance** on the PR — e.g.
   `AI-assistance: Copilot (Relentless, flash/LOW)`. Every AI-originated PR
   declares this.
5. **Report evidence**, never an unverified "done": the exact command + output,
   files touched, and the issue it closes (GR-12).

## PR checklist

- [ ] Branch is `issue-<n>-<slug>` based on latest `master`
- [ ] Touches only files owned by the issue's lane (GR-3)
- [ ] Issue `Verify:` command ran and output is included
- [ ] `make verify` green locally
- [ ] No secrets / credentials / build artifacts committed (GR-6)
- [ ] AI-assistance + runtime declared
- [ ] Docs kept in sync (architecture / execution plan / governance)
- [ ] PR body includes `Closes #<n>`

## Review & merge

- Every change lands via a PR. Under the owner's autonomous-merge mandate an
  agent may merge its own PR **only after** green `make verify` evidence —
  never merge failing work (GR-12). Branch protection/required checks land
  with issue #6.

## Governance asks

1. **Issue-first** — every task lands on the issues board before the work.
2. **IaC over console** — infra changes land as Terraform/Cloud Build PRs, not
   console clicks (GR-5).
3. **No secrets** — env/secret manager only; the secret scan runs in
   `make verify` (GR-6).
4. **Provenance first** — cannibalized assets record source (GR-10).
5. **Verify before done** — evidence, not assertion (GR-12).
