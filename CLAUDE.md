# CLAUDE.md — agent-orchestrator (Claude Code pointer)

Claude Code working in this repo. **Read `AGENTS.md` first** — it is the
canonical, model-agnostic instruction file; this file only adds
Claude-specific notes and never contradicts it.

## What this repo is

`agent-orchestrator` = the **AI-agent-orchestration service control plane**
(EPIC-00, issue #4): a multi-tenant SaaS control plane that organizes, governs,
and manages commercial AI agents (Claude, DeepSeek, Copilot, Gemini, local
Ollama). Five-pillar architecture — Agent Registry & Profiling (`registry/`),
Model Gateways (`gateway/`), State-machine execution (`engine/`), Security &
guardrails (`guardrails/`), Observability (`telemetry/`) — plus cross-cutting
tenant identity/RBAC (`identity/`), control-plane/portal (`control-plane/`,
`portal/`), and autonomous ops/governance. Default branch is `master`.

## Commands

```bash
make verify   # gate of record: shell syntax + YAML + JSON + docs + secrets
make help     # list all targets
bash -n <file>.sh
```

## Claude-specific notes

- **Precedence:** `AGENTS.md` is canonical. This file and `.cursorrules` defer
  to it; never contradict it (supersede doctrine in `AGENTS.md`).
- **Subagent-first + FinOps** (fleet doctrine): delegate to subagents at the
  cheapest capable tier (flash/LOW default; escalate on observed difficulty,
  never pre-emptively). One issue = one lane = one branch = disjoint files
  (`docs/EXECUTION-PLAN.md`). An orchestrating lead does not write lane code
  inline.
- **Local-code-first (GR-17):** debug against this checkout first.
- **Never:** push directly to `master`; commit secrets (env only, GR-6); run
  ad-hoc `terraform apply` (GR-5); merge failing work (verification evidence
  first, owner autonomous-merge mandate); edit `vendor/` (pinned submodule);
  commit `.research/` clones (gitignored).
