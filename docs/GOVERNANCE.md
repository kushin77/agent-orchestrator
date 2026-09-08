# Governance — agent-orchestrator

Lean, enforceable governance for the AI-agent-orchestration control plane.
The goal: every change is **tracked, lane-owned, verified, and reversible**,
and each pillar keeps a stable contract so any agent/product surface can join.

> **Pointers:** agents start at [`../AGENTS.md`](../AGENTS.md); the five-pillar
> architecture is [`ARCHITECTURE.md`](ARCHITECTURE.md); parallel dispatch is
> [`EXECUTION-PLAN.md`](EXECUTION-PLAN.md). This repo is a governed fleet repo;
> where it is silent, `kushin77/CMR` golden rules (GR-1…GR-24) bind.

## 1. Branch rules

| Branch | Rules |
|--------|-------|
| `master` | Default branch. Protected **by convention**: no direct pushes, no force-push. Every change lands via a PR (squash merge). Branch protection as code ships with issue #6. |
| `issue-<n>-<slug>` | Short-lived work branches (e.g. `issue-5-repo-foundation`). Merged and deleted. |

One branch per issue; a pushed branch must be backed by an open PR or be
deleted (anti-sprawl).

## 2. Session labels & provenance (AI-originated work)

AI-originated issues, PRs, commits, and doc sections declare their source:

- **Session labels:** `[CLI]` / `[COPILOT]` / `[DESKTOP]` / `[HUMAN]` where a
  channel needs disambiguation.
- **AI-assistance declaration:** every AI-originated PR carries an
  `AI-assistance: <runtime> (<mode>)` note (e.g.
  `AI-assistance: Copilot (Relentless, flash/LOW)`).
- **Commit references (GR-2):** every commit references its issue with
  `Refs kushin77/agent-orchestrator#<n>`; every PR closes one with `Closes #<n>`.
- **Provenance (GR-10):** cannibalized/harvested assets record source (repo,
  path, license) in the cannibalization index (issue #8). `vendor/CMR` is a
  pinned submodule; `.research/` clones are gitignored.

## 3. Review & merge doctrine

- Every change lands via a PR. No self-approval ceremony is required under the
  **owner autonomous-merge mandate (2026-09-07)**: an agent merges its own PR
  **only after** green verification evidence (GR-12). **Never merge failing
  work.** Verification replaces the human reviewer in this fleet.
- Merging without green `make verify` is forbidden in all cases.

## 4. Review checklist (every PR)

- [ ] Branch is `issue-<n>-<slug>` based on latest `master`
- [ ] Only files owned by the issue's lane (`docs/EXECUTION-PLAN.md`)
- [ ] Gate of record: `make verify` passes (output attached as evidence)
- [ ] No secrets (covered by the gate scan), no build artifacts, no `vendor/`
      edits, no `.research/` commits
- [ ] Docs kept in sync (architecture / execution plan / governance)
- [ ] AI-assistance + runtime declared
- [ ] `Closes #<n>` referenced

## 5. Roadmap & work intake

- All planned work = GitHub issues on this repo's board (EPIC-00 = issue #4
  indexes everything; issue labels encode `phase:N`, `pillar:X`, `priority`,
  `type`).
- Every task — planned or ad-hoc — lands on the board before the work (GR-2 /
  board mandate). A commit without an issue reference is a finding.
- Closed issues are closed **with evidence** (the gate output that proved the
  change).

## 6. Lessons (living)

Operational learnings are captured as they surface in:
- `docs/adr/` — durable architectural decisions (issue #7 seeds the ADR
  process),
- memory notes and issue comments — session/repo memory conventions.

A lesson is written down the moment a change reveals it — never "at the end".

## 7. Standing obligations

1. **Gate of record** — `make verify` is the gate until CI lands (issue #6);
   it is honest (no-false-green).
2. **IaC-only live infra** — every live surface is declared (Terraform/Cloud
   Build); ad-hoc console changes are debt and get promoted or retired.
3. **Secrets** — env/secret-manager only; never commit secrets or pass them
   through argv/history.
4. **Flag-gated OFF by default** — new product surfaces ship invisible until
   deliberately enabled.
5. **FinOps** — dispatch at the cheapest capable tier; declared services carry
   resource limits.
