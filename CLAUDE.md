# CLAUDE.md — CMR (Claude Code pointer)

## Connections

- **Owner-lane:** qa-sme
- **Class:** class
- **Connects-to:** consumes=none; called-by=none; gates=none
- **Env:** none
- **Updated-by:** qa-sme (2026-09-12)
- **Landed-by:** 2f022c3

This file is the **Claude Code** runtime pointer for CMR. It contains only
Claude Code-specific invocation context. **Read `AGENTS.md` first — it is the
canonical, model-agnostic instruction file; do not contradict it.** The
model-agnostic policy, the runtime → file map, and the drift check live in
`docs/MODEL-AGNOSTIC.md`.

## Claude Code-specific context

- Claude Code auto-reads this file from the repo root; it is the only
  Claude-specific entry point. All behavior, frontloading, memory, and facts live in
  `AGENTS.md`.
- `.claude/settings.json` is Claude Code's local deny list — it reinforces the
  human/CI-gated action that stays hard-blocked at every scale: `terraform apply`.
  `git push` is allowed at the solo-dev scale (ADR-0020) — `make scale-tripwire`
  (GR-4 carve-out) already watches the reinstatement trigger (committer count /
  onboarded-spoke count vs. the 10 threshold); re-add the deny entry when it fires.
  The deny list is a safety default; `AGENTS.md` (Hard DON'Ts) and `GOLDEN-RULES.md`
  are the rule.
- Dispatch Claude subagents per the FinOps ladder in `docs/MODEL-PROFILES.md`
  (flash-by-default in ladder terms — the concrete Claude Code model is
  **haiku**, set as the project default via `.claude/settings.json`'s
  `CLAUDE_CODE_SUBAGENT_MODEL`; escalate on observed difficulty, never
  pre-emptively, per `guardrails/instructions/model-tier-discipline.md`).

## Pointers

- Canonical rules: `AGENTS.md` (read first) → `GOLDEN-RULES.md` → `board/INTENT.md` →
  `docs/ARCHITECTURE.md`.
- Run the issue's `Verify:` command and `make verify` before merge/close. Merge/push
  posture is defined in `GOLDEN-RULES.md` GR-4 — it is not restated here.
- Local guardrails (hard-deny floor): `guardrails/hooks/{shell-aware-deny,scale-aware-allow}.sh`;
  code-native gate `make guardrails-check`. Runtime permission modes + bypass notes:
  `docs/PERMISSIONS-OPERATIONS.md`.
- Defaults: `docs/DEFAULTS.md` · No-questions doctrine: GR-22 + `guardrails/instructions/no-questions.md` (apply defaults, escalate only per GR-22).
- Agent operating doctrine: `guardrails/instructions/agent-operating-doctrine.md`.
