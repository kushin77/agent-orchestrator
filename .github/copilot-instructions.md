# Copilot instructions — agent-orchestrator (GitHub Copilot pointer)

This file is the **GitHub Copilot** runtime pointer for
`kushin77/agent-orchestrator`. It contains only Copilot-specific invocation
context. **Read `AGENTS.md` first** — it is the canonical, model-agnostic
instruction file for this repo; this file never contradicts it.

## What this repo is

`agent-orchestrator` is the **AI-agent-orchestration service control plane**
(EPIC-00, issue #4): a multi-tenant SaaS control plane that organizes, governs,
and manages commercial AI agents (Claude, DeepSeek, Copilot, Gemini, local
Ollama). Five-pillar architecture — Agent Registry & Profiling (`registry/`),
Model Gateways (`gateway/`), State-machine execution (`engine/`), Security &
guardrails (`guardrails/`), Observability (`telemetry/`) — plus cross-cutting
tenant identity/RBAC (`identity/`), control-plane/portal (`control-plane/`,
`portal/`), and autonomous ops/governance (`governance/`, `infra/`). Default
branch is `master`. See `docs/ARCHITECTURE.md` for the source of truth.

## Commands

```bash
make verify   # gate of record: shell syntax + YAML + JSON + docs + secrets
make help     # list all targets
bash -n <file>.sh
```

## Copilot-specific notes

- **Precedence:** `AGENTS.md` is canonical. `CLAUDE.md`, `.cursorrules`, and
  this file defer to it and never contradict it. `docs/GOLDEN-RULES.md`
  (AO-GR-1..20) is the canonical product spine; root `GOLDEN-RULES.md` records
  the CMR vendor-module ratification.
- **Workflow:** read the issue (its `Verify:` command and lane ownership); touch
  only your lane's files; smallest focused diff; run `make verify` and report
  actual output as evidence before claiming done; declare AI-assistance +
  runtime on the PR.
- **Local-code-first (GR-17):** debug against this checkout first; query the
  **CMR indexer KB** for cross-repo/org-wide `kushin77` knowledge.

## Hard DON'Ts

- No direct pushes to `master`; changes land via a PR against `master` (squash
  merge). No merging failing work — green `make verify` evidence required first.
- No secrets in code, files, or git history (env / secret managers only).
- No ad-hoc `terraform apply` / console clicks; new infra ships flag-gated OFF.
- No editing `vendor/` (pinned CMR submodule); never commit `.research/`
  clones; no unfinished markers or debug leftovers.
