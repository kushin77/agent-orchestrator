# DEEPSEEK.md — CMR (DeepSeek pointer)

## Connections

- **Owner-lane:** qa-sme
- **Class:** class
- **Connects-to:** consumes=none; called-by=none; gates=none
- **Env:** none
- **Updated-by:** qa-sme (2026-09-12)
- **Landed-by:** 2f022c3

This file is the **DeepSeek** runtime pointer for CMR. It contains only DeepSeek-specific
invocation context. **Read `AGENTS.md` first — it is the canonical, model-agnostic
instruction file; do not contradict it.** The model-agnostic policy, the runtime → file
map, and the drift check live in `docs/MODEL-AGNOSTIC.md`.

## DeepSeek-specific context

- DeepSeek is invoked as an agent runtime via MCP (`mcp__deepseek__*` tools) or as a
  subagent dispatch target in CMR sessions. This file serves as the DeepSeek-specific
  entry point.
- Dispatch work per the FinOps ladder in `docs/MODEL-PROFILES.md` (flash-by-default;
  escalate on observed difficulty, never pre-emptively).
- Local guardrails hard-deny floor: `guardrails/hooks/` (shell-aware + scale-aware
  PreToolUse deny); code-native gate `make guardrails-check`. Runtime permission modes:
  `docs/PERMISSIONS-OPERATIONS.md`.

## Pointers

- Canonical rules: `AGENTS.md` (read first) → `GOLDEN-RULES.md` → `board/INTENT.md` →
  `docs/ARCHITECTURE.md`.
- Run the issue's `Verify:` command and `make verify` before claiming done. Merge/push
  posture is defined in `GOLDEN-RULES.md` GR-4 — it is not restated here.
- Defaults: `docs/DEFAULTS.md` · No-questions doctrine: GR-22 + `guardrails/instructions/no-questions.md` (apply defaults, escalate only per GR-22).
- Agent operating doctrine: `guardrails/instructions/agent-operating-doctrine.md`.
