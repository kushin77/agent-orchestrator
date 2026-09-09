# Changelog

All notable changes to `agent-orchestrator` are tracked here. Versioning and
release mechanics follow [`RELEASING.md`](RELEASING.md) (SemVer `vX.Y.Z` git
tag on `master` = source of truth, GR-7). Releases are annotated tags at a
gate-green commit; this file is a human-readable summary and is never the
release source of truth.

## [v0.1.0] — 2026-09-08

Initial release of the **AI-agent-orchestration service control plane** (EPIC-00
blueprint, issue #4): a multi-tenant SaaS control plane that organizes, governs,
and manages commercial AI agents (Claude, DeepSeek, Copilot, Gemini, local
Ollama). Full build-out across the five pillars plus cross-cutting surfaces
(phases 0-8, ~47 PRs). All product surfaces ship **flag-gated OFF** by default
(`infra/feature-flags/registry.yaml`); nothing is tenant-visible until a
reviewed go-live promotes it.

Highlights by phase:

- **Phase 0 — Foundations**: repo scaffold + agent-instruction layering,
  golden-rules product spine (AO-GR-1..20), CI/CD + IaC foundation
  (flag-gated OFF, `make verify`), cannibalization index (issue #5-#8, #50-#53).
- **Phase 1 — Agent Registry & Profiling** (`registry/`): AgentProfile schema +
  validator + versioning, prompt/instruction library, SME persona registry,
  Agent Identity + Registry service, tenant onboarding/provisioning (#9-#14).
- **Phase 2 — Model Gateways** (`gateway/`): multi-provider client adapters
  (anthropic/deepseek/gemini/ollama/openai), FinOps model chooser (L0/L1/L2),
  semantic cache + token budget + rate limit + output throttle, model-health
  monitoring, tenant-scoped MCP gateway, proxy route/dispatch/log funnel (#15-#20).
- **Phase 3 — State-machine execution** (`engine/`): task lifecycle + job
  queue, scoped agent memory, durable state-machine orchestration
  (namespaces/saga/resume), deterministic agent-loop runtime, multi-agent
  orchestration (#21-#25).
- **Phase 4 — Security & guardrails** (`guardrails/`): policy-as-code + gate
  engine (BLOCK/WARN/LOG), DLP scrub + prompt-injection defense + egress guard,
  sandboxed agent tool execution, tenant-isolation integrity, guard-honesty
  model (negative controls, no-false-green) (#26-#30, #58).
- **Phase 5 — Observability** (`telemetry/`): tamper-evident per-tenant audit
  ledger, usage metering + cost engine, per-tenant budgets/quotas + global kill
  switch, full-trace telemetry + per-tenant SLOs (#31-#34).
- **Phase 6 — Tenant identity/RBAC** (`identity/`): tenant SSO (per-tenant
  SAML/OIDC, scoped-claims sessions, JWKS, impersonation), RBAC role model,
  entitlements/plan tiering, control-plane REST API + event/outbox bus, public
  API + proxy allowlist boundary (#35-#38).
- **Phase 7 — Control plane / portal** (`control-plane/`, `portal/`): admin/
  tenant console (design tokens + SSO), consumer SDK + born-compliant templates
  (Python `aosdk` + TypeScript + MCP), model-agnostic instruction layer (#39-#42).
- **Phase 8 — Governance / rollout / e2e**: AgentPack registry + catalog,
  merge/PR governance + SME reviewer, sync/drift engine + provenance manifests,
  flag-gated rollout + deployment pipeline, QA gate stack (`make gate`,
  `qa-loop`, `merge-gate`), E2E negative-control + multi-provider golden path
  (#29, #40, #43-#45, #88-#97).

**Governance / distribution:**

- `LICENSE`: MIT (Copyright (c) 2026 kushin77).
- `module.json`: CMR vendor-module manifest (`cmr.module/v1`,
  `id: agent-orchestrator`, `type: service`) — schema-valid against
  `vendor/CMR/catalog/schemas/module.schema.json`.
- Guardrail set ratified: root `AGENTS.md`/`CLAUDE.md`/`.cursorrules`/
  `.github/copilot-instructions.md` + root `GOLDEN-RULES.md` ratification
  pointer (hub GR-1..18 bind where the repo spine is silent).
- CMR vendor onboarding: `CMR:ONBOARD-0012` (issue #98).

[`RELEASING.md`](RELEASING.md) describes how to cut future releases.
