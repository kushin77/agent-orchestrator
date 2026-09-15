# CANNIBALIZATION — 24-repo harvest map (source index for EPIC-00)

> **Status:** living index · **Harvest date:** 2026-09-08 · **Parent:** EPIC-00 (Phase 0) · **Issue:** kushin77/agent-orchestrator#8

This is the distilled source map for building the **AI Agent Orchestration SaaS**
(EPIC-00): which of the ~24 harvested repos holds what, where the reusable
assets live, and where the canonical copy of duplicated assets should be.
Every implementing agent **reads this file first** before starting a phase, so no
feature is built from scratch while a harvested asset already covers it.

**Raw scans are NOT in git.** The full harvest reports live under
`.research/reports/` (`CMR-harvest.md`, `leaderboard-harvest.md`,
`fleet/*-harvest.md`) — gitignored by design (see [Auto-refresh](#auto-refresh-note)).
This file is the only committed artifact.

---

## 0. Verdict legend

| Verdict | Meaning |
|---|---|
| **READY** | Drop-in / near-drop-in asset (generic machinery, contracts, schemas, check-sets). Swap identifiers and it works. |
| **PATTERN** | The *shape / architecture / taxonomy / algorithm* is the asset; implementation is tied to the source repo's substrate (bash/GitHub, Go, single-tenant) and must be redesigned for a multi-tenant SaaS. |
| **REFERENCE** | Document / decision / knowledge asset worth citing and adapting — not copied as code. |

Verdicts are distilled **faithfully from the harvest reports**, never from file names.

---

## 1. Source inventory (the 24 reports)

Local clones are under `/home/akushnir/agent-orchestrator/.research/` —
top-level repos (`CMR`, `leaderboard`) and `fleet/<name>` for the rest. All paths
below are **repo-relative** unless prefixed with `CMR/` or `leaderboard/`.

| # | Report file (`.research/reports/…`) | Repo | Local clone | Shape / status |
|---|---|---|---|---|
| 1 | `CMR-harvest.md` (452 ln) | `kushin77/CMR` | `.research/CMR` | Mature single-tenant control-plane "hub" (~390 files) — richest pattern source |
| 2 | `leaderboard-harvest.md` (428 ln) | `kushin77/leaderboard` | `.research/leaderboard` | Battle-tested multi-agent fleet (675 bash scripts) — richest logic source |
| 3 | `fleet/ai-agents-harvest.md` | `kushin77/ai-agents` | `.research/fleet/ai-agents` | **STUB** (3 doc files) → recover from eiq-ai upstream |
| 4 | `fleet/aiops-engine-harvest.md` | `kushin77/aiops-engine` | `.research/fleet/aiops-engine` | **STUB** (3 doc files) → recover from eiq-ai upstream |
| 5 | `fleet/capital-underwriting-harvest.md` | `kushin77/capital-underwriting` | `.research/fleet/capital-underwriting` | Mature multi-tenant SaaS core + fleet FinOps overlay |
| 6 | `fleet/code-indexing-harvest.md` | `kushin77/code-indexing` | `.research/fleet/code-indexing` | Compiler-accurate indexer + MCP tool catalog (Python/SQLite) |
| 7 | `fleet/defragsuite-harvest.md` | `kushin77/defragsuite` | `.research/fleet/defragsuite` | Infra-automation monorepo — guardrail engine + control-plane portal |
| 8 | `fleet/dprs-harvest.md` | `kushin77/dprs` | `.research/fleet/dprs` | **≡ git-rca-workspace** (byte-identical) — see §4 duplicate flags |
| 9 | `fleet/git-rca-workspace-harvest.md` | `kushin77/git-rca-workspace` | `.research/fleet/git-rca-workspace` | RCA / workflow / RBAC / observability engine + portal |
| 10 | `fleet/gmail-agent-harvest.md` | `kushin77/gmail-agent` | `.research/fleet/gmail-agent` | Governed commercial-agent (Claude) reference: tiering, triage, audit |
| 11 | `fleet/gov-ai-scout-harvest.md` | `kushin77/Gov-AI-Scout` | `.research/fleet/gov-ai-scout` | AI-provider gateway + typed-output contracts (Ollama-only) |
| 12 | `fleet/hermes-agents-harvest.md` | `kushin77/hermes-agents` | `.research/fleet/hermes-agents` | Agent registry / routing / model-tiering (Python+TS) |
| 13 | `fleet/intelligence-harvest.md` | `kushin77/intelligence` | `.research/fleet/intelligence` | **STUB** (partial extraction, 3 files) → recover from eiq-ai |
| 14 | `fleet/issue-aggregator-harvest.md` | `kushin77/issue-aggregator` | `.research/fleet/issue-aggregator` | **STUB** (partial extraction, 6 files) → recover from eiq-ai |
| 15 | `fleet/llm-triage-harvest.md` | `kushin77/llm-triage` | `.research/fleet/llm-triage` | Archived but **self-contained** triage classifier (5 files) |
| 16 | `fleet/monitoring-stack-harvest.md` | `kushin77/monitoring-stack` | `.research/fleet/monitoring-stack` | Observability/SLO/FinOps platform |
| 17 | `fleet/news-feed-engine-harvest.md` | `kushin77/news-feed-engine` | `.research/fleet/news-feed-engine` | Multi-tenant content platform (Go tenancy + aspirational agent core) |
| 18 | `fleet/ollama-harvest.md` | `kushin77/ollama` | `.research/fleet/ollama` | Local-LLM platform: resilient client, usage/cost, DLP |
| 19 | `fleet/saas-rbac-harvest.md` | `kushin77/saas-rbac` | `.research/fleet/saas-rbac` | **THE multi-tenant RBAC / identity reference** (Node/Fastify) |
| 20 | `fleet/shared-frontend-harvest.md` | `kushin77/shared-frontend` | `.research/fleet/shared-frontend` | OS-portal + fleet-standard persona/model trio + design tokens |
| 21 | `fleet/shared-governance-harvest.md` | `kushin77/shared-governance` | `.research/fleet/shared-governance` | Governance policy-as-code hub (Python engine + frozen schemas) |
| 22 | `fleet/shared-services-harvest.md` | `kushin77/shared-services` | `.research/fleet/shared-services` | On-prem multi-tenant platform — egress guard, govctl, MCP hub |
| 23 | `fleet/shared-temporal-harvest.md` | `kushin77/shared-temporal` | `.research/fleet/shared-temporal` | Durable orchestration patterns (Temporal) + cost/SLA governance |
| 24 | `fleet/vscode-memory-harvest.md` | `kushin77/vscode-memory` | `.research/fleet/vscode-memory` | Agent memory / prompt-control-plane (Qdrant + policy gates) |

> **Note on "24":** the count is the 24 report files above (2 top-level + 22
> fleet). `kushin77/dprs` is byte-identical to `git-rca-workspace`, and the
> `eiq-ai` monorepo (`elevatediq-ai/eiq-ai`) is the upstream home of the
> extraction stubs (spot-harvested only; no dedicated report file). These are
> resolved in §4–§5 so nothing is double-counted.

---

## 2. Per-pillar reuse map

Pillars align to EPIC-00 (foundations / autonomous-ops, agent-registry &
profiling, model-gateway, state-machine execution, guardrails-security,
observability-finops, identity-rbac, control-plane, infra-IaC). A repo can
appear in several pillar tables — that is expected for a source map.

**Path bases:** `CMR/…` → `.research/CMR/…`; `leaderboard/…` →
`.research/leaderboard/…`; otherwise the repo's own clone under
`.research/fleet/<repo>/…` (e.g. `saas-rbac/services/backend-api/prisma/schema.prisma`
→ `.research/fleet/saas-rbac/services/backend-api/prisma/schema.prisma`).

### 2.1 Foundations · blueprint & autonomous-ops doctrine

| Source repo | Key path(s) | Verdict | Canonical-copy / notes |
|---|---|---|---|
| CMR | `GOLDEN-RULES.md` (GR-1…GR-24), `docs/MODEL-AGNOSTIC.md`, `docs/SAAS-BLUEPRINT.md`, `docs/SAAS-BOUNDARY.md`, `docs/DEFAULTS.md`, `canibalization/INDEX.md` + `registry.csv`, `board/materialize.sh` | PATTERN | The control-plane policy spine + SaaS escape-hatch doctrine. CMR is the single-tenant **reference instantiation**; generalize repo→tenant. Harvest-discipline ledger (`canibalization/registry.csv`) is READY-grade. |
| leaderboard | `docs/reference/RULES.md`, `config/policy.yaml`, `config/policies/rules/*.yaml` + `invariants/*.yaml`, `scripts/guard/policy-check.sh`, `scripts/ops/push-lesson.sh`, `docs/retrospectives/LESSONS_2026-07-26_ELITE.md`, `docs/reference/FAILURE_TAXONOMY.md` | READY | 12 universal rules + policy-as-code + lessons→guards→policy loop; failure taxonomy = DLP alert taxonomy (RULES.md + retro lessons are REFERENCE; policy/config + lesson pipeline READY). |
| shared-frontend | `docs/EXECUTION-PLAN.md`, `docs/GOLDEN-RULES.md`, `AGENTS.md` | READY | One-issue-one-lane dispatch contract + codified rules-with-verification. |
| shared-governance | `GLOBAL_STANDARDS/agent-orchestration-model.md`, `GLOBAL_STANDARDS/governance-distribution.md` | PATTERN | Worker-tier orchestration + tenant pin-never-fork distribution doctrine. |

### 2.2 Agent registry & profiling (identity records, personas, SME lenses, capabilities)

| Source repo | Key path(s) | Verdict | Canonical-copy / notes |
|---|---|---|---|
| leaderboard | `docker/worker-fleet/personas.yaml`, `lib/fleet-identity.sh`, `lib/leaderboard-registry.sh`, `lib/lenses/*` (8 files), `docs/reference/PLATOON_LEADER.md`, `config/platoons.yaml` | READY | Persona=role declarative model; one-file-per-agent session registry; **lens = SME persona as discriminating questions** — the persona-catalog model. |
| CMR | `onboarding/agent-profiles/role.schema.json` + `validate.sh` + `profiles/*.json`, `guardrails/agents/*.agent.md`, `docs/SME-PROFILES.md` | PATTERN | Closest existing thing to a SaaS agent-identity record. Persona-card frontmatter + `validate.sh` READY; `SME-PROFILES.md` → duplicate trio (see §4). |
| hermes-agents | `src/hermes_agent/services/capability_registry.py`, `models/capability_registry.py`, `api/capabilities.py` | READY | `agents` + `task_routes` tables = direct template for the multi-tenant **agent catalog** (+`tenant_id`, RBAC cols). |
| capital-underwriting | `scripts/agent/sme/*.txt` (10 personas) + `sme-dispatch.sh` | READY | Drop-in per-file persona system-prompts. |
| shared-governance | `governance/identity/agent-identities.json`, `identity-model.yml`, `GLOBAL_STANDARDS/agent-identity.md`, `schemas/agent-identity-jwt.schema.json` | READY | Agent identity model + JWT claims contract. |
| shared-frontend | `docs/SME-PROFILES.md` | READY | Fleet-standard persona cards → duplicate trio (see §4). |
| defragsuite | `.defrag/agent-intelligence-score.json` | PATTERN | Agent capability-profile score (6 dims + evidence). |
| ollama | `_legacy/group_a/agents/templates.py` | PATTERN | Agent-specialization factory → SME-persona registry pattern. |
| news-feed-engine | `services/ai-personas/README.md`, `services/multi-agent-system/README.md` | PATTERN | Persona archetype template + registry/consensus specs (submodule stubs — spec only). |
| vscode-memory | `scripts/hermes-capability-registry.py` | PATTERN | Agent capability/skill catalog per persona. |
| code-indexing | `docs/SME-PROFILES.md` | READY | Duplicate trio copy — canonical TBD (see §4). |

### 2.3 Model gateway (routing, tiers, providers, cost chooser)

| Source repo | Key path(s) | Verdict | Canonical-copy / notes |
|---|---|---|---|
| leaderboard | `lib/fleet-roster.sh`, `scripts/elite/finops-router.sh`, `lib/complexity-scorer.sh`, `scripts/elite/output-throttle.sh`, `lib/semantic-cache.sh`, `lib/llm-cache.sh`, `config/deepseek-capabilities.txt`, `lib/orchestrator.sh`, `docs/reference/FINOPS_ARCHITECTURE.md` | READY | **The L0/L1/L2 model-chooser**: role→tier→model, complexity, security auto-escalate, semantic cache, per-task token caps; capability-catalog schema (`FINOPS_ARCHITECTURE.md` = REFERENCE). |
| CMR | `docs/MODEL-PROFILES.md`, `fleet/dispatch.sh model`, `fleet/cost-report.sh` | READY | FinOps model ladder + cost telemetry READY; `fleet/dispatch.sh model` router-seed = PATTERN. `MODEL-PROFILES.md` → duplicate trio (see §4). |
| gov-ai-scout | `backend/src/services/ai-provider.ts`, `ai-integration-config.ts`, `services/rfp-analysis.ts` | READY | Provider gateway + per-task model registry + fallback/retry + zod-validated structured outputs. |
| hermes-agents | `models/model_tiering.py`, `services/model_tier_selector.py`, `services/escalation_handler.py`, `extension/src/modelSelection.ts` | READY | Tier/ceiling/cost model + complexity→tier routing + escalation. |
| ollama | `ollama/services/inference/resilient_ollama_client.py`, `ollama/services/resilience/circuit_breaker.py`, `ollama/api/schemas/*.py` | READY | Circuit-breaker LLM client + typed inference contracts. |
| gmail-agent | `src/agent/claude.ts` | READY | Claude client: model tiers (`AI_PRIMARY/COMPLEX/FALLBACK`), p-retry, agentic tool loop, zod output. |
| defragsuite | `pkg/defrag-ai/modal/client.go` | READY | LLM gateway client: circuit breaker + cloud→local fallback. |
| capital-underwriting | `scripts/agent/lib/finops-router.sh`, `apps/server/src/lib/aiCostRates.ts` | READY | Task-tier router + per-provider rate card. |
| llm-triage | `src/llm_triage/classifier.py` | READY | Provider-neutral classifier (OpenAI+Claude behind one ABC), cache + retry. |
| shared-services | `automation/contract_help/egress-proxy-gateway.py` | PATTERN | Outbound LLM egress guard (audit + model gate before vendor). |

### 2.4 State-machine execution (durable orchestration, queues, DAG, saga, actor loops)

| Source repo | Key path(s) | Verdict | Canonical-copy / notes |
|---|---|---|---|
| leaderboard | `lib/agent-loop.sh`, `lib/executor.sh`, `lib/orchestrator.sh`, `scripts/dispatch/fanout.sh`, `agent-dispatch-{enqueue,pull,ack}.sh`, `requeue-orphans.sh`, `scripts/fleet/dag-plan.sh` + `dag-runner.sh`, `lib/resilience.sh`, `lib/net.sh`, `lib/single-writer-lock.sh` | READY | Deterministic actor/runner ReAct loop, route→dispatch→guard→log pipeline, claim/ack queue, DAG executor, dead-claim recovery. **The SaaS state machine + tool-execution boundary.** |
| CMR | `fleet/queue.sh`, `fleet/wip-guard.sh`, `ops/run.sh`, `ops/retry.sh`, `sync/gdc-feed.sh`, `controller/auto-merge.sh` | READY | Near-drop-in disk job queue (SQLite swap), retry lib, scheduler (change-feed + merge-governance state machine = PATTERN). |
| shared-temporal | `patterns/multi_tenancy.go`, `patterns/advanced_patterns.go` (Saga), `patterns/saas_subscriptions.go`, `patterns/README.md`, `ENTERPRISE_PATTERNS_INDEX.md`, `governance/cost-tracking.ts`, `governance/sla-enforcement.ts` | READY | Durable per-tenant provisioning, saga/compensation, subscription lifecycle (`distributed_failover.go` + federation = PATTERN). |
| git-rca-workspace (≡ dprs) | `src/core/workflow_engine.py`, `src/core/template_engine.py`, `src/services/temporal_workflows.py` | PATTERN | Templated multi-step workflow definitions + Temporal scaffolding. |
| capital-underwriting | `scripts/agent/*-daemon.sh`, `scripts/dispatch/fanout.sh`, `fleet_api_server.py` | PATTERN | Worker/queue/control-plane pattern (queue pull → persona → route → call → audit). |
| defragsuite | `services/agents/analyst-agent/*` | PATTERN | Task lifecycle state machine (`DETECTED→…→COMPLETED`) + multi-source intake. |
| gmail-agent | `src/queue/tasks.ts` | PATTERN | Idempotent typed background-task queue. |
| news-feed-engine | `services/processor/processor/ai_agents.py` (1,585 ln) | PATTERN | Best-in-class agent state machine + typed message bus + decision audit — **aspirational** (imports a missing module); harvest architecture not bytes. |
| intelligence | `ensemble_scorer.py` | PATTERN | Multi-model consensus scoring spec (agreement/dominant/weighted) — can't run standalone (see §5). |
| issue-aggregator | `deduplication.py`, `main.py` | READY | Dedup engine logic (exact/semantic/fuzzy/provenance, weighted confidence); FastAPI ingest service is PATTERN (see §5). |

### 2.5 Guardrails & security (no-false-green gates, DLP, policy-as-code, secrets, audit gates)

| Source repo | Key path(s) | Verdict | Canonical-copy / notes |
|---|---|---|---|
| leaderboard | `scripts/guard/` (60+ guards), `lib/guard.sh`, `lib/guard-status.sh`, `scripts/qa/qa-gatekeeper.sh` + `signals.d/`, `scripts/qa/verify-negative-controls.sh`, `corpus/pass` + `corpus/fail`, `scripts/audit/validate-auditor.sh`, `guard-separation-of-duties.sh` | READY | The anti-false-green family + guard predicate library + 3-state exit + negative-control methodology. **Product differentiator.** QA gate vendored from testing-suite (canonical: leaderboard copy in use). |
| CMR | `guardrails/policy/controls.yaml` (+schema+validator), `guardrails/gates/` (approval/telemetry/compliance/SLO), `guardrails/policy/secrets/`, `guardrails/check-conformance.sh`, `guardrails/policy/terraform/`, `ops/export/` (9 egress checks), `.gitleaks.toml` | READY | Controls registry (one row per control, default OFF) = guardrail-toggle catalog; secrets/terraform DLP + gate set; export egress suite = PATTERN→SaaS. |
| shared-governance | `GLOBAL_STANDARDS/external-llm-egress-policy.md`, `GLOBAL_STANDARDS/schemas/scrub-rules.yml`, `schemas/agent-action.schema.json`, `GLOBAL_STANDARDS/approval-gate.md`, `governance/policy-dsl/*` | READY | Scrub-then-revalidate commercial-agent guardrail doctrine + frozen action-record contract + policy DSL/versioning. |
| defragsuite | `pkg/defrag-ai/interceptor.go`, `services/legacy/defrag_ai/policies/policies.yml` (+schema), `pkg/defrag-ai/modal/prompts.yaml` | READY | Executable guardrail interceptor (`BLOCK/WARN/LOG`, pattern rules, LLM enrichment) + policy DSL + validated prompt library. |
| shared-services | `automation/contract_help/audit_hmac.py`, `response_gateway.py`, `automation/govctl/*` (audit sign/verify/query/alert) | READY | Tamper-evident HMAC call audit + audit/compliance control plane (`egress-proxy-gateway`/`response_gateway` = PATTERN). |
| ollama | `ollama/services/security/dlp_redactor.py`, `docs/agent-quality-standards.md` | READY | GCP-DLP PII redaction of prompts/responses (agent-quality-standards doc = REFERENCE → policy). |
| vscode-memory | `scripts/preexec-policy-gate.py`, `scripts/audit_logger.py`, `gate-event-logger.py`, `gate-metrics.py` | READY | Pre-execution policy gate + audit/gate-event/metrics logging. |
| git-rca-workspace (≡ dprs) | `src/policy/opa_client.py`, `src/scanners/secretScanner.ts` | READY | OPA policy-as-code enforcement point (`secretScanner.ts` = PATTERN). |
| capital-underwriting | `apps/server/src/lib/auditChain.ts`, `secureAuditLog.ts`, `accountIsolationRepair.ts` | READY | Per-tenant tamper-evident audit hash chain + tenant-isolation repair. |
| code-indexing | `codeidx/secret_scan.py`, `policy/*` (check.py + rego) | READY | Never-index-secrets pre-publish gate; policy-as-code with conftest→opa→python fallback. |
| gov-ai-scout | `backend/src/middleware/rateLimit.ts`, `auditLog.ts` | PATTERN | Tenant-level rate limiting + immutable audit middleware. |

### 2.6 Observability & FinOps (telemetry, metering, SLO/SLA, budgets, chargeback)

| Source repo | Key path(s) | Verdict | Canonical-copy / notes |
|---|---|---|---|
| leaderboard | `lib/tracing.sh`, `lib/fleet-log.sh`, `scripts/fleet/check-fleet-state-fresh.sh`, `check-daemon-freshness.sh`, `loop-outcome-check.sh`, `config/fleet-daemons.json`, `config/finops-budget.yaml`, `lib/token-counter.sh`, `scripts/elite/ab-harness.sh`, `scripts/guard/metrics-anomaly.sh` | READY | Liveness-vs-outcome doctrine, staleness watermarks, budgets, cost attribution, model A/B eval. |
| CMR | `scripts/health.sh` + `health-report.sh`, `ops/sla-monitor.sh`, `ops/hygiene.sh`, `guardrails/gates/slo/`, `fleet/cost-report.sh` | READY | Control-plane health reporter + SLA/hygiene monitors + SLO definitions. |
| monitoring-stack | `slo-framework/templates/*` (60+), `slo-framework/calculator/slo_calculator.py` + `breach_detector.py`, `scripts/chargeback-report-generator.py`, `dashboards/finops-burn-rate.json`, `billing-cost-tracking.json`, `scripts/cloud-function-anomaly-detection.py` | READY | Per-tenant SLO YAML seed + Grafana dashboards; calculators/chargeback/anomaly scripts = PATTERN. |
| shared-temporal | `governance/cost-tracking.ts`, `governance/sla-enforcement.ts` | READY | Run-cost accounting + SLA enforcement primitives. |
| capital-underwriting | `apps/server/src/lib/aiUsage.ts` | READY | Per-tenant AI token accounting + daily budget (observe→enforce toggle). |
| shared-services | `cost-governance/budget_enforcer.py`, `automation/telemetry/global_pause.py`, `services/tenant-slo-exporter/main.py`, `services/resource-quota-enforcer/quota_enforcer.py` | READY | Per-vendor budget enforcement + global kill-switch + per-tenant SLO/quotas. |
| ollama | `ollama/api/routes/usage.py`, `ollama/services/cost/service.py` + `collector.py` | READY | Per-user usage/cost analytics API (cost snapshot/forecast service = PATTERN). |
| hermes-agents | `monitoring/prometheus/rules/application-alerts.yml`, `src/hermes_agent/log.py` | REFERENCE | Alert names/thresholds (application-alerts.yml); `log.py` SLOG convention = PATTERN. |
| git-rca-workspace (≡ dprs) | `src/services/jaeger_tracing.py`, `structured_logging_service.py`, `utils/logging_config.py` | READY | Tracing + structured logging + correlation IDs. |
| gov-ai-scout | `backend/src/lib/apm.ts`, `config/telemetry.ts` | PATTERN | APM/metrics hooks. |
| shared-governance | `GLOBAL_STANDARDS/schemas/rate-limit-telemetry.schema.json` + `telemetry-budget.schema.json` | READY | Leading-indicator budget/rate-limit metrics contracts. |
| news-feed-engine | `docs/MODEL_CARD_VIRALITY_SCORING.md`, `MODEL_CARD_TREND_FORECASTING.md` | REFERENCE | Model-card discipline (block-below-threshold quality gates). |

### 2.7 Identity & RBAC / multi-tenancy

| Source repo | Key path(s) | Verdict | Canonical-copy / notes |
|---|---|---|---|
| saas-rbac | `services/backend-api/prisma/schema.prisma`, `src/rbac/{types,resolve,guard,bindings,presets}.ts`, `src/tenants/provisioning.ts`, `frontend-api/src/auth/tenant-resolution.ts` + `tenant-mapping.ts`, `src/billing/entitlements.ts` + `overrides.ts`, `frontend-api/src/proxy.ts`, `SCHEMA_STRATEGY.md`, `docs/ARCHITECTURE.md` | READY | **THE multi-tenant RBAC/identity reference.** `resource:action` permission language, no-lockout invariant, idempotent tenant provisioning, SaaS⇄IdP tenant mapping, plan→entitlement→override. Canonical for this pillar. |
| shared-governance | `governance/multi-tenancy/{tenant_manager,rbac_engine,policy_inheritance,quota_manager,audit_logger}.py`, `governance/authorization-system/{rbac,abac,approval_workflows,time_restrictions}.py`, `governance/enterprise-multi-tenancy/*`, `schemas/agent-oidc-config.schema.json` | READY | Hierarchical RBAC/ABAC engine, approval workflows, time windows, per-tenant audit isolation + agent OIDC. |
| capital-underwriting | `apps/server/prisma/schema.prisma` (`Account/AccountUser/Subscription/Entitlement/AuditLog`), `middleware/{auth,attachAuth,planGate,requireFeature,aiRateLimit}.ts`, `mcp/capitalMcpServer.ts` | READY | Tenant/plan/entitlement data model + layered auth/RBAC + **tenant-scoped MCP tool server**. |
| CMR | `docs/IDENTITY.md`, `controller/teams.tsv`, `onboarding/saas-rbac/0007-module-onboarding.md` | REFERENCE | CMR's *actual* RBAC implementation lives in the separate `saas-rbac` repo (consumed as a module) — do not look here for RBAC code (`IDENTITY.md`/`teams.tsv` = PATTERN). |
| git-rca-workspace (≡ dprs) | `src/services/rbac_service.py`, `utils/rbac.py` | READY | Role/permission RBAC model + OAuth2/TOTP middleware (PATTERN). |
| shared-frontend | `docs/AUTH.md`, `auth/` | READY | Control-plane SSO + session-token model (auth-hub relay, HttpOnly cookie). |
| news-feed-engine | `services/news-feed-engine/internal/middleware/middleware.go`, `internal/models/models.go` | READY | Header/subdomain tenancy + per-tenant rate limiter (Go). |
| shared-temporal | `patterns/multi_tenancy.go` | READY | Durable per-tenant provisioning/isolation workflows. |
| leaderboard | `scripts/guard/guard-separation-of-duties.sh`, `docs/reference/PLATOON_LEADER.md` (role=permission) | READY | RBAC-by-role + separation-of-duties (control plane never executes). |
| ollama | `ollama/auth/zero_trust.py`, `policy.py` | PATTERN | Zero-trust auth policy for tenant identity boundaries. |

### 2.8 Control plane (portal, CLI, MCP hub, feature flags)

| Source repo | Key path(s) | Verdict | Canonical-copy / notes |
|---|---|---|---|
| CMR | `portal/index.html` + `assets/cmr-state.js` + `gen-state.py`, `portal/views/controls-panel.js`, `merge-gate-toggle.js`, `cli/cmr`, `templates/frontend/shell/` | PATTERN | Admin control-plane UI skeleton (projection-not-SSOT) + `agentctl`-style scaffolder (`gen-state.py` = READY). Shell vendored from shared-frontend. |
| shared-frontend | `modules/schema.json`, `registry/modules.json`, `shared/design-tokens/tokens.css`+`tokens.json`, `docs/DESIGN-TOKENS.md` | READY | Plugin/module registry contract + design tokens (CSS+JSON twins w/ provenance). |
| shared-services | `services/mcp-hub/server/mcp-server.ts` + `index.ts` + `config.ts` | READY | MCP gateway control plane (JWT + rate-limit + per-tool telemetry). |
| capital-underwriting | `scripts/agent/fleet_api_server.py`, `apps/server/src/mcp/capitalMcpServer.ts` | READY | **Tenant-scoped MCP tool server** (`capitalMcpServer.ts`); fleet API control plane = PATTERN. |
| defragsuite | `services/defrag-portal/main.py`, `pkg/defrag/feature_flags.go` | READY | Flag/rollout engine (`feature_flags.go`); policy "command center" portal = PATTERN. |
| leaderboard | `scripts/mcp/execution-guard.sh`, `scripts/cto/*` + `.cto/config.yaml` | READY | Drop-in governance overlay (`.cto/`); MCP execution-guard = PATTERN (tool-call interception). |
| code-indexing | `codeidx/mcp_server.py`, `tool_registry.py`, `api.py`, `gateway.py` | READY | Reference MCP tool catalog — canonical, AST-scoped agent tools. |
| shared-governance | `governance/control-plane/control_plane_registry.py`, `rollout_orchestrator.py`, `eiq_governance/` | READY | Governed-surface registry + self-serve compliance CLI (rollout orchestrator = PATTERN). |
| git-rca-workspace (≡ dprs) | `portal-backend/app/` | PATTERN | Control-plane REST portal structure (routers/schemas). |
| saas-rbac | `frontend-api/src/proxy.ts` | READY | Explicit allowlist + path-traversal guard for the public→internal boundary. |

### 2.9 Infra / IaC (delivery, per-tenant infrastructure, policy-as-code for infra)

| Source repo | Key path(s) | Verdict | Canonical-copy / notes |
|---|---|---|---|
| CMR | `infra/terraform/github/` (+8 modules), `infra/cloudbuild/`, `infra/harvest/policy-as-code/` (rego), `infra/harvest/kubernetes/`, `infra/harvest/` (SLSA provenance, landing-zone, onprem) | PATTERN | GitHub governance-as-code + delivery (harvested OPA rego, SLSA provenance, machine-identity module = READY). |
| saas-rbac | `infra/terraform/modules/*` (auth, database, compute, edge, ci-identity, secrets, observability) | REFERENCE | Multi-tenant SaaS GCP IaC blueprint (re-shape, don't copy). |
| shared-services | 2-node HA platform (Docker + Terraform: Postgres/Patroni, Redis/KeyDB, Vault, HAProxy) | REFERENCE | HA multi-tenant substrate layout (Docker/Terraform topology to reshape). |
| monitoring-stack | `alerts/kubernetes.rules.yml`, `charts/alert-templates/`, `terraform/` | READY | Alert-rule authoring (`kubernetes.rules.yml`); Helm alert templates + terraform = REFERENCE. |
| leaderboard | `terraform/leaderboard-tenant/{main.tf,…}` | REFERENCE | Per-tenant infra pattern (IaC per tenant). |
| gov-ai-scout | `.copilot-instructions` (GCP landing-zone compliance checklists) | REFERENCE | Compliance checklist content for the SaaS's own GCP landing zone. |

---

## 3. Canonical-copy & duplicate-asset flags

These are **not** resolved in this index — they are flagged so a later lane or
decision records the single source of truth **before** two phases adopt two
different copies.

### 3.1 Fleet-standard trio: `MODEL-PROFILES` / `SME-PROFILES` / `SOLUTION-CLASSES` — canonical copy **TO BE DECIDED**

The identical doc trio exists in **at least three repos** (confirmed present on
disk):

| Copy | Path | Report note |
|---|---|---|
| CMR | `CMR/docs/MODEL-PROFILES.md`, `CMR/docs/SME-PROFILES.md`, `CMR/docs/SOLUTION-CLASSES.md` | Most mature / most-cited (CMR is the hub; MODEL-PROFILES marked "core doc for the SaaS model chooser"). |
| shared-frontend | `shared-frontend/docs/MODEL-PROFILES.md`, `SME-PROFILES.md`, `SOLUTION-CLASSES.md` | Labeled the **"fleet-standard trio"** — persona/model/quality docs. |
| code-indexing | `code-indexing/docs/MODEL-PROFILES.md`, `SME-PROFILES.md`, `SOLUTION-CLASSES.md` | Report explicitly says *"verify which copy is canonical before adopting"*. |

**Action:** a decision record (or the phase-0/phase-1 owner) must pick one
canonical home (CMR is the natural hub) and make the other two reference-only
pointers, so the product's persona/model/quality docs don't drift across three
vendored copies. Until decided, treat **CMR as the working default** and flag any
adoption with the provenance line `harvested_from: <repo>/<path>` (GR-10).

### 3.2 `dprs ≡ git-rca-workspace` — byte-identical (confirmed)

`kushin77/dprs` ("Developer Platform Reference System") is a **byte-identical
copy** of `kushin77/git-rca-workspace`. Verified against the clones:
recursive file-listing diff = 0 lines and `README.md` md5 equal (both
`158e52b38ed8521427f8779918482fe9`). **Harvest everything from
`git-rca-workspace` only**; do not double-count `dprs` assets in the product BOM.
If "DPRS" is the intended *platform reference-architecture name*, treat
`docs/EPIC-001-ARCHITECTURE.md`, `docs/WORKFLOW_ENGINE_ARCHITECTURE.md`,
`docs/ORG_MEMORY_ENGINE.md` in the git-rca clone as that seed.

### 3.3 Other known duplication (context, not blocking)

- **shared-frontend OS shell** is vendored into `CMR/templates/frontend/shell/`
  → canonical source is `shared-frontend`.
- **leaderboard CTO overlay** is vendored into `capital-underwriting/vendor/`
  (FinOps router, guards) → canonical source is `leaderboard`.
- **`qa-gatekeeper.sh`** was vendored from `testing-suite` into leaderboard →
  the in-use canonical copy is `leaderboard/scripts/qa/qa-gatekeeper.sh`
  (adapted; testing-suite is the upstream lineage).

### 3.4 Code-index surfaces — authority & the no-re-derivation rule (pointer)

The relationship between three index-shaped surfaces — this repo's institutional
catalogue (`governance/knowledge/`), the declared in-memory symbol graph in
`gateway/mcp/kb.py`, and the real `kushin77/code-indexing` index — is **decided in
[`decision-records/ADR-0018`](decision-records/ADR-0018-codeidx-consumption-and-index-authority.md)**:
which surface owns institutional vs code facts, the gateway's posture, and the
frozen rule that a shape in a published contract is *consumed, never mirrored*.
The harvest entries above record **provenance only**; under that rule a
`harvested_from` line is a citation, never a licence to re-implement.

---

## 4. Recovery pointers — stub / partial-extraction repos → eiq-ai upstream

Four harvested repos are extraction stubs whose real source moved to the
**eiq-ai monorepo** (`elevatediq-ai/eiq-ai`, `packages/…`). Treat the local
clones as **evidence that the capability exists**, not as reusable code, and
recover upstream before designing the corresponding product layer from scratch:

| Stub repo | Local evidence | Recover from | Product relevance if recovered |
|---|---|---|---|
| `ai-agents` | 3 doc files only; README: "Composable AI agent framework" (Python), dev moved upstream | `elevatediq-ai/eiq-ai/packages/ai-agents` | **Closest-named repo to the product core** (agent framework). In-clone substitutes for its patterns meanwhile: hermes-agents, gmail-agent. |
| `aiops-engine` | 3 doc files only; "AIOps observability + anomaly-detection engine" | `elevatediq-ai/eiq-ai/packages/aiops-engine` | Agent-telemetry anomaly alerting (only if wanted; the product's observability slice already covers the base need). |
| `intelligence` | Partial extraction: `ensemble_scorer.py` + `prompt_tuner.py` only; imports `.smart_selector/.llm_auto_triage/.semantic_clustering` absent | eiq-ai upstream monorepo (triage suite) | Ensemble-of-models consensus scoring; prompt-variant A/B tuning. |
| `issue-aggregator` | Partial extraction: `main.py` + `deduplication.py` import `models/schema/connectors.*` absent (6 files) | eiq-ai upstream monorepo | Ingestion→normalize→dedup layer for agent inputs; dedup algorithm itself is READY locally. |

Adjacent note: **`llm-triage`** is archived (README redirects to
`elevatediq-ai/eiq-ai/packages/llm-triage`) but is **self-contained** locally —
its classifier/few-shot/feedback modules are usable without recovery.

---

## 5. Auto-refresh note

- **Cadence:** re-run the harvest scan on a fixed cadence (quarterly is the
  current default) **and** whenever a repo joins or leaves the fleet, or before a
  new phase-owner starts pulling assets. Each phase kickoff should diff this
  index against the live `.research/reports/` to catch drift.
- **Keep the raw scan out of git.** Full harvest reports live under the
  gitignored `.research/reports/`; only this distilled index is committed. When
  a *new repo* joins the fleet, produce its `*-harvest.md`, then update this
  index (source inventory + per-pillar rows + duplicate flags) in the same PR.
- **Provenance on every adoption (GR-10):** any asset copied into the product
  must carry `harvested_from: <repo>/<path>` + harvest date, so the BOM can be
  re-audited against this map.
- **Re-verify verdicts, don't assume them:** verdicts in this index are faithful
  to the 2026-09-08 reports. If a report is thin (stubs above), this index says
  so instead of inventing assets.

---



## 6. M26 session-fleet harvest (issue #161) — roles, vocabulary, transport

The session fleet operating model (brain → fleet-brain sister → epic-focused
subagents) is consumable prior art, not new vocabulary. Harvested **2026-09-13**
from local checkouts, read-only. Both source repos are `kushin77`-owned and both
carry the license **"Copyright (c) 2026 kushin77. All Rights Reserved.
PROPRIETARY — INTERNAL USE ONLY"** (`LICENSE` at each repo root) — the same owner
as this repo. What is reused is therefore the **pattern and the vocabulary**,
re-implemented for this repo's Python/JSON substrate; no source file was copied,
so no `harvested_from` code marker applies. The derived artifacts are
`fleet/CONTRACT.md` and `docs/decision-records/ADR-0011-session-fleet-transport.md`.

| Source (repo-relative) | Asset | Verdict | Where it landed |
|---|---|---|---|
| `leaderboard/lib/fleet-roster.sh` | ROLE / TIER / MODEL / TRANSPORT / EFFORT separation, `role_capability_tier` (deep/balanced/fast), per-model capability profiles | PATTERN | `fleet/CONTRACT.md` §1 — a seat is not a model; the four attributes travel in the directive's FinOps block instead of being inferred by the executor |
| `leaderboard/docs/LEADERBOARD_PROTOCOL.md` | per-session row files, one worktree per session (id = branch), a reporter that *computes* status, "a done claim must match a real gate result" | REFERENCE | `fleet/CONTRACT.md` §0/§6 + ADR-0011 — coordination stays inside the repo's own checkout, no broker |
| `leaderboard/docs/CTO_OVERLAY.md` | four-layer blocking-governance overlay, per-layer severity and the `.cto/config.yaml` tier system | PATTERN | ADR-0011 — governance layers run in-repo; a layer that cannot block a merge is advisory, which is the same rule this repo's `make verify` follows |
| `capital-underwriting/docs/wiki/ELITE_GENERAL_CHARTER.md` | commander → general → sniper chain of command; "never substitute 'looks fine' for a gate result" | REFERENCE | `fleet/CONTRACT.md` §4 (trust rules) and §6 (enforcement) |
| `capital-underwriting/docs/wiki/ELITE_PLATOON_LEADER_CHARTER.md` | wave ownership, mutually-disjoint file sets, role → N-1 hand-off | REFERENCE | `fleet/CONTRACT.md` §2 (`dispatch-issue`, `handoff`) |
| `capital-underwriting/scripts/agent/sme/*.txt` + `sme-dispatch.sh` | per-file adversarial reviewer prompts (auditor, security, terraform, sniper-generic, prisma-db, test-quality) | READY | epic-subagent reviewer roles — `registry/personas` and the FinOps chooser (#164) |
| `vendor/CMR` (pinned submodule) | hub M9 (`board/MILESTONES.md`, `board/epics/EPIC-11-github-full-surface.md`) | REFERENCE | ADR-0011 — read to check the A2A option, and **not** usable as an A2A wire contract: the hub's M9 is GitHub full-surface governance |

**Also enumerated, not read in full:** `capital-underwriting/docs/wiki/` carries
five `ELITE_*.md` charters (`AUDITOR`, `COMMANDER_LTC`, `GENERAL`,
`PLATOON_LEADER`, `SCRIBE`); two were read in full for this issue (the two in the
table above) and the remaining three were listed only — no content from them is
reproduced here.

**Missing / unreadable source — reported, not invented:**
`capital-underwriting/.claude/agents/*.md`. That path holds **no agent
markdown**: it contains three runtime session directories
(`agent-7bfd1589/session.json`, `agent-e418370a/session.json`,
`relentless-765/session.json`) plus a `.gitkeep`. The SME cards the issue's
harvest map refers to are the `scripts/agent/sme/*.txt` files listed above. No
`.claude/agents` content was read or reused.

**Deliberately not harvested here:** `personas.yaml`,
`capability-registry.json` and `tier-policy.json` / `route-policy.json` belong to
the #163/#164 lanes. #161 takes the role vocabulary and the transport decision
only, so two lanes cannot drift apart on the same asset.


## 7. M26 FinOps chooser harvest (issue #164) — tier and thinking-effort vocabulary

The FinOps vocabulary the brain directs subagents with is **prior art, not new
coinage**. Harvested **2026-09-13** from read-only local checkouts of the two
mature `kushin77` repos. Both are owner-authored and carry the license
**"Copyright (c) 2026 kushin77. All Rights Reserved. PROPRIETARY — INTERNAL USE
ONLY"** (`LICENSE` at each repo root) — the same owner as this repo. What is
reused is therefore the **pattern and the vocabulary**, re-implemented for this
repo's Python/JSON substrate; no source file was copied, so no `harvested_from`
code marker applies. The derived artifacts are `governance/finops/policy.json`,
`governance/finops/chooser.py` (the chooser and its enforcement) and
`scripts/check-finops-chooser.sh` (the gate).

| Source (repo-relative) | Asset | Verdict | Where it landed |
|---|---|---|---|
| `capital-underwriting/config/leaderboard/tier-policy.json` | `tiers{}` → `flash` / `pro` / `auditor`, their model ids (`deepseek-v4-flash` / `deepseek-v4-pro`), timeout and context limits, `complexity_to_tier` thresholds | PATTERN | `governance/finops/policy.json` — the tier names and `tier_models` are adopted verbatim |
| `capital-underwriting/config/leaderboard/route-policy.json` | route → `model_tier` mapping (`fast`→flash, `deep`→pro, `strict`→auditor) plus `dispatch_defaults` | PATTERN | `governance/finops/policy.json` `tier_rank` (auditor is the escalation tier) and the subagent allowlist |
| `leaderboard/lib/fleet-roster.sh` | ROLE / TIER / MODEL / TRANSPORT / EFFORT separation, `role_effort()` = `low` / `medium` / `high`, `role_capability_tier()` = deep / balanced / fast, and the rule that nothing else may hardcode a model per role | PATTERN | the thinking-effort names; the chooser derives all four attributes from one directive block instead of letting an executor infer them |
| `leaderboard/scripts/elite/finops-router.sh` | cheapest-capable-tier routing (T2 = flash, T3 = pro + thinking), with a forced tier treated as a privileged override | PATTERN | refusal of a self-chosen tier: here the only issuer is the brain's directive, so an agent's own request can never be honoured |
| `capital-underwriting/config/leaderboard.config.example` | the fleet's thinking-off state (`DEEPSEEK_THINKING` / `LB_THINKING_DEEPSEEK` = `enabled` / `disabled`) | REFERENCE | `none` in the vocabulary is that disabled state, which is already this repo's standing-directive value |

**Adopted verbatim (no new coinage).** Tiers `flash | pro | auditor` — exactly
the `tiers{}` keys above. Thinking effort `low | medium | high` — exactly
`role_effort()`'s values; plus `none`, the fleet's thinking-off state, which is
not a coinage either: it is the DSv4FNone seat this repo already states in
`fleet/directive.json`. The gate pins all four names, so a rename in the policy,
the message schema, the channel or the docs fails `make verify`.

**Deliberately not harvested here.** `capability-registry.json` and
`personas.yaml` belong to the #163 dispatcher lane; `LEADERBOARD_PROTOCOL.md`,
the CTO overlay and the `ELITE_*` charters belong to the #161 / #165 / #166
lanes. #164 takes the FinOps vocabulary and its enforcement only, so two lanes
cannot drift apart on the same asset.

**Numbering note.** §6 is the M26 session-transport harvest recorded by the
issue #161 lane; this section is numbered above it rather than reusing 6, so the
two lanes cannot collide on a heading.

## 8. Issue-brief harvest — the fleet-task template (issue #165)

The canonical issue brief (`.github/ISSUE_TEMPLATE/fleet-task.yml`) is harvested,
not invented: its field set and FinOps vocabulary are lifted from two
kushin77-internal sources. **Both sources are kushin77 proprietary / internal use
only. They were read to learn the FORMAT only — no file, code, or prose was
copied into this repo, and nothing from them is committed here.** Verdicts follow
§0.

| Source repo | Key path(s) | Verdict | What the brief takes from it |
|---|---|---|---|
| leaderboard | `lib/fleet-roster.sh` (`FLEET_ROLES`, `role_model`, `role_tier`, `role_capability_tier`, `role_effort`) | PATTERN | Role-scoped execution: a task declares a **role**, and the role pins its **model**, **capability tier** (`deep`/`balanced`/`fast`) and **reasoning effort** (`high`/`medium`/`low`), each overridable per run (`ROLE_MODEL_<ROLE>`, `ROLE_TIER_<ROLE>`). The brief reproduces the *shape* — role + tier + effort declared on the issue itself, never inferred later. |
| leaderboard | `scripts/dispatch/spec-lint.sh` (+ `agent-dispatch-enqueue.sh`) | PATTERN | Pre-dispatch spec lint: a task is refused at enqueue when its brief is incomplete (missing `ISSUE`/`SCOPE`/`TASK_TYPE`/`FILES`, or an issue body with no `## Acceptance (mechanical)` block). This is the precedent for `scripts/check-issue-template.sh` — the brief is linted, not trusted, and it fails loud with a named reason. |
| leaderboard | `.github/ISSUE_TEMPLATE/enhancement.md` | PATTERN | House brief skeleton (a Scope with affected-component / breaking-change / migration sub-fields; a checkbox `## Acceptance Criteria`; an `## Additional Context` evidence slot) mirrored by the template's acceptance-criteria and evidence fields. |
| capital-underwriting | `docs/wiki/ELITE_*.md` (`ELITE_PLATOON_LEADER_CHARTER.md`, `ELITE_AUDITOR_CHARTER.md`, `ELITE_GENERAL_CHARTER.md`, `ELITE_COMMANDER_LTC_CHARTER.md`, `ELITE_SCRIBE_CHARTER.md`) | REFERENCE | The ELITE charter structure (role line → companion-document links → blocking thesis → numbered mandate sections → a per-cycle checklist) and its **Model Tiering** table, where each role maps to a model + reasoning effort and the highest-stakes seat is explicitly held *above* the cheap tier. That table is the source of the brief's `model-tier` / `thinking-effort` pairing. |

**Vocabulary adopted verbatim (no local synonym):**

- **Model tier** = `flash` / `pro` / `auditor`. `flash` and `pro` are this
  repo's FinOps tier names; `auditor` is the measured audit seat the charters
  require be held at the higher-reasoning tier (`ELITE_AUDITOR_CHARTER.md` §4 —
  never downgrade the Auditor to the flash tier).
- **Thinking effort** = `none` / `low` / `medium` / `high`. `low`/`medium`/`high`
  are the leaderboard `role_effort()` returns; `none` is the fleet's
  thinking-off state.

The brief is enforced, not merely documented: `scripts/check-issue-template.sh`
(wired into `make verify` and `make lint`) fails, by name, when a required field
is missing or the vocabulary drifts, and it runs its own negative control so it
cannot pass vacuously (GR-12).

**Provenance (GR-10):** `harvested_from:` `kushin77/leaderboard`
(`lib/fleet-roster.sh`, `scripts/dispatch/spec-lint.sh`,
`.github/ISSUE_TEMPLATE/enhancement.md`) and `kushin77/capital-underwriting`
(`docs/wiki/ELITE_*.md`) — pattern/REFERENCE only, kushin77 proprietary /
internal use only, no code copied.


## 9. Never-idle dispatcher harvest (issue #163)

The dispatcher loop, its whitelist enforcement and the "never picks its own
work" doctrine were **already shipped** under adjacent issue numbers before
this issue's implementing agent was assigned: `fleet/terminal.py` (issues
#186/#189/#190/#192) is the persistent `while True` poll loop that never exits
on IDLE, `fleet/channel.py` `validate()` is the whitelist enforcement (message
type, control action, and FinOps vocabulary allowlists — an out-of-vocabulary
directive is refused with a named reason, not silently dropped), and
`governance/dispatch/cli.py held` is the "never re-dispatch in-flight work"
check `terminal.py` calls before spawning. This section records the one piece
of the issue's acceptance criteria those PRs did not cover: the **health
signal**.

| Source (repo-relative) | Asset | Verdict | Feeds |
|---|---|---|---|
| `leaderboard/docker/worker-fleet/personas.yaml` | `fleet-health` persona: a health-report daemon kept separate from the fanout/executor/supervisor roles, polling on its own interval | PATTERN | `fleet/health.py` — a standalone read-only check, not folded into `terminal.py`'s loop |
| `capital-underwriting/config/leaderboard/capability-registry.json` | agent role / tool / tier registry with per-role `latency_tier` and `cost_tier` | REFERENCE only | **not harvested as PATTERN** — that registry's roles (`planner`/`executor`/`verifier`/`critic`) are a different role vocabulary than this repo's contract (`brain`/`sister`/`subagent`, `fleet/CONTRACT.md` §1), so importing its shape would create a second, drifting taxonomy. `governance/finops/policy.json` (issue #164) is already this repo's role/tier registry. |

`fleet/health.py` implements the cmr-style tri-state the issue names
(`0 healthy / 1 degraded / 2 failing`, matching the repo's existing exit-code
convention documented in `fleet/channel.py`'s module docstring): `2` when the
`terminal.py` process is not running at all, `1` when it is running but the
slog has gone stale or a claim has been held past the staleness window
(wedged — the same condition `governance/dispatch cli.py reap` recovers from),
`0` otherwise. It is read-only: it never spawns a subagent and never touches
the mailbox, so it cannot itself become a second dispatcher. Wired into
`fleet/control.py health` for operator use; `fleet/tests/test_health.py`
covers all three signal levels.

## 10. Five-agent team routing + claude/anthropic catalog entry (issue #255)

The gateway routing for the five-agent team (`ollama`, `paperclip`, `hermes`,
`deepseek`, `claude`) and the claude/anthropic module-catalog entry are
**consumed prior art**, harvested **2026-09-13** from read-only local checkouts
of kushin77-owned repos. What is reused is the **pattern and the vocabulary**,
re-implemented for this repo's Python/YAML substrate; no source file was
copied, so no `harvested_from` code marker applies.

| Source (repo-relative) | Asset | Verdict | Where it landed |
|---|---|---|---|
| `kushin77/gmail-agent` `src/agent/claude.ts` | Claude client: model tiers, p-retry, agentic tool loop, zod output | PATTERN | `gateway/providers/anthropic.py` (existing) + the new gateway-owned `gateway/catalog/modules/claude-anthropic/module.json` entry, closing the 4-of-5 gap left by #124 |
| `kushin77/ollama` `ollama/services/inference/resilient_ollama_client.py` | Ollama `/api/chat` wire shape, circuit-breaker client | READY | `gateway/providers/hermes.py` — the hermes adapter reuses the Ollama-compatible request/response shape (frozen map `hermes -> hermes/ollama`) |
| `kushin77/gov-ai-scout` `backend/src/services/ai-provider.ts` | Provider gateway with per-task model registry + fallback/retry | PATTERN | `gateway/providers/paperclip.py` — the paperclip adapter reuses the OpenAI-compatible `chat/completions` shape shared with `deepseek`/`openai` |
| `kushin77/hermes-agents` `models/model_tiering.py` + `api/capabilities.py` | agent -> model/provider routing tables | READY | `gateway/proxy/config/routing.yaml` `routingGroups.purebliss-team` — agent -> provider + fallback/retry, consumed from the EPIC #253 frozen map |

The `purebliss-team` routing group pins each agent to its provider
(`ollama -> ollama`, `paperclip -> paperclip`, `hermes -> hermes/ollama`,
`deepseek -> deepseek`, `claude -> anthropic`) with the local Ollama hop as the
terminal fallback, and is enforced in `gateway/proxy/router.py` (a group agent
is routed by its pinned chain, not the tier chain). The claude/anthropic
catalog entry lives under `gateway/catalog/` (gateway-owned) rather than the
pinned `vendor/CMR/catalog/modules/` submodule, which this lane must not edit.

**Provenance (GR-10):** `harvested_from:` `kushin77/gmail-agent`
(`src/agent/claude.ts`), `kushin77/ollama`
(`ollama/services/inference/resilient_ollama_client.py`),
`kushin77/gov-ai-scout` (`backend/src/services/ai-provider.ts`),
`kushin77/hermes-agents` (`models/model_tiering.py`, `api/capabilities.py`) —
pattern/READY only, kushin77 proprietary / internal use only, no code copied.
## 11. Five-agent team harvest (issue #254) — profiles, personas, purebliss-team pack

Issue #254 registers the five-agent team (`ollama`, `paperclip`, `hermes`,
`deepseek`, `claude`) as first-class registry artifacts: five AgentProfile
seeds under `registry/profiles/seeds/`, five PersonaCards under
`registry/personas/cards/`, and one AgentPack
(`registry/packs/releases/purebliss-team.1.0.0.yaml`, publisher
`platform/purebliss`, category `team`) that bundles the ten artifacts behind a
signed PS256 attestation. Every profile/persona consumes **only** the closed
vocabulary already in `registry/profiles/catalog.yaml` (no new vocabulary ids),
and the new `team` pack category is added to both
`registry/packs/agent-pack.schema.json` and `registry/packs/pack-catalog.yaml`
(schema↔catalog parity).

| Source (repo-relative) | Asset | Verdict | Feeds |
|---|---|---|---|
| `ollama/services/inference/resilient_ollama_client.py` + `services/resilience/circuit_breaker.py` | resilient local-LLM client + circuit breaker | PATTERN | `ollama` profile/persona (LOW tier, local inference, executor posture) |
| `llm-triage/src/llm_triage/classifier.py` | provider-neutral classifier behind one ABC (cache + retry) | PATTERN | `paperclip` profile/persona (research/docs-authoring, provider-neutral) |
| `hermes-agents/src/hermes_agent/services/capability_registry.py` + `models/model_tiering.py` | capability-tagged routing + tier/ceiling/cost model | READY | `hermes` profile/persona (code-author/test-author, MED tier) |
| `leaderboard/lib/fleet-roster.sh` + `scripts/elite/finops-router.sh` + `config/deepseek-capabilities.txt` | role→tier→model chooser + DeepSeek capability catalog | READY | `deepseek` profile/persona (research/data-analysis, MED tier) |
| `gmail-agent/src/agent/claude.ts` | governed Claude client (model tiers, p-retry, agentic tool loop) | READY | `claude` profile/persona (orchestrate/code-author/review, MED tier) |
| `CMR/catalog/schemas/module.schema.json` + `registry/packs/*` | signed per-version bundle attestation (PS256, `kid`/`alg`/`signedAt`) | READY | `purebliss-team` pack attestation (reuses `registry/packs/attestation.py`) |

**Publisher-key rotation note (GR-6).** The private half of the pack publisher
key (`kid ao-pack-publisher-v1`) was never committed and is not recoverable, so
this issue's implementing agent generated a fresh RSA-2048 keypair, committed
the new public key to `registry/packs/publisher-key.pem`, re-signed the three
pre-existing release snapshots (`data-ops`, `orchestrator-ops`,
`worker-platform` — contents untouched, only the attestation signature
changes), and signed the new `purebliss-team` snapshot with the same new key.
The private key is discarded after signing (GR-6: env/secret-manager-only at
publish time); all four release signatures verify against the committed public
key via `registry/packs/validate.py`.

## 12. Leaderboard worker-fleet hardening harvest (issue #240)

The worker-fleet hardening idioms the orchestrator's own ops lane will adopt are
**consumed prior art**, read-only, harvested **2026-09-13** from the local clone of
`kushin77/leaderboard` at `/home/akushnir/leaderboard` (`HEAD` `f7bc4715`). The
source repo carries the license **"Copyright (c) 2026 kushin77. All Rights
Reserved. PROPRIETARY — INTERNAL USE ONLY"** (`LICENSE` at its root) — the same
owner as this repo. What is reused is therefore the **pattern**, re-implemented for
this repo's Python/JSON substrate; **no source file was copied** into this repo, so
no `harvested_from` code marker applies. Verdicts follow §0. The issue named
`docker/worker-fleet` + `scripts/fleet` as the source; **each cited path was opened
and checked** and the tables below say which named pattern is where — including one
the issue's phrasing implies is in a different file (it is not).

| Source (repo-relative) | Asset | Verdict | What we adopt |
|---|---|---|---|
| `docker/worker-fleet/fleet.cron` | `flock -n -E 99` **distinct-exit singleton idiom** — a cron tick guarded by a non-blocking lock whose conflict exit is `99`, so "skipped, lock held" is distinguishable from "ran and failed" | PATTERN | A scheduled job's skip must not look like a failure. Adopt an explicit, out-of-band skip exit code rather than a bare `flock -n` (whose conflict exit `1` is indistinguishable from the guarded command failing, and prints nothing). The measured backstory is in the file's own comment: four concurrent `qa-gatekeeper --strict` invocations aged 422s/303s/183s/63s at 198% CPU on 2026-08-14 — a `*/2` tick outliving four ticks. |
| `scripts/fleet/fleet-up-verify.sh` | **tri-state exit codes** — `0` success, `1` usage/verify failure, `2` cannot-assess (a required dependency, `docker-api.sh`, is missing) | PATTERN | Our ops scripts keep the `0 OK / 1 NOT-OK / 2 CANNOT-ASSESS` contract used across this repo's own gates; a script that cannot evaluate its subject must say so, never report a pass. |
| `docker/worker-fleet/docker-compose.worker-fleet.yml` | **`secrets.env` bind-mount contract** — secrets are mounted read-only into the container, deliberately **not** listed under `env_file` | PATTERN | A secret is mounted or injected, never placed in the container/service spec where `docker inspect` / `compose config` would print it. The file states the rule and the reason: `env_file` puts the values in the spec, so a bind-mount at `:ro` is the contract. (The same file's `env_file:` block carries only non-secret config.) |
| `docker/worker-fleet/entrypoint.sh` | **`GH_TOKEN` non-clobber guard** — `export GH_TOKEN="${GITHUB_PERSONAL_ACCESS_TOKEN:-$GH_TOKEN}"`, re-exporting the alternate only when it is actually set | PATTERN | Never overwrite a populated credential with an empty one. The file records the incident: an unconditional re-export clobbered a correctly-populated `GH_TOKEN` with an empty string on every boot, silently breaking every raw `git fetch`/`push` while `gh auth status` still looked fine. Our env/secret handling keeps the same rule. |
| `docker/worker-fleet/Dockerfile` | **digest-pinned base image + build-time validation** — `FROM node:22-slim@sha256:6c74791e…` and a build-time `RUN` that checks a manifest of required fleet scripts is present and parses the compose YAML, so a missing script fails the **build**, not the running fleet | PATTERN | Pin by digest, not a floating tag, and validate at build time. Adopt the intent (fail the build, not the fleet). **Reported honestly:** the digest pin is on the *main* `Dockerfile` only — the sibling `docker/worker-fleet/hot-executor.Dockerfile` and `docker/worker-fleet/pool-sidecar/Dockerfile` both use `FROM alpine:latest`, i.e. **unpinned**. The named pattern is present but not applied uniformly in the source; we adopt it uniformly. |
| `docker/worker-fleet/git-guard.sh` | **destructive-git guard** — a `git` wrapper installed at `/usr/local/bin/git` (earlier in `PATH` than `/usr/bin`) that blocks `reset --hard`, `clean`, and `worktree add/remove/prune` on bind-mounted repos, passing everything else through | PATTERN | A shared/self-hosted checkout needs a mechanical guard against destructive git ops; the source's own root-cause note is a container that ran `git reset origin/<branch>` and **deleted** `scripts/`, `lib/`, `config/`, `docker/` from the host tree. Our lanes already forbid force-push/history rewrite; this is the mechanical precedent for putting the refusal in front of the command. |
| `scripts/fleet/docker-api.sh` | **Docker Engine API via curl over the unix socket** — programmatic container status/restart/logs/stats, cheaper and more consistent than shelling out to the compose CLI; carries its own security note that the socket is host-root-equivalent | PATTERN | Talk to the daemon's API directly for read/scrape paths rather than parsing CLI output; and treat the socket mount as the privileged thing it is. Only the *shape* is reusable here — this repo's FleetOps lane is Python and container-free by #239. |
| `config/fleet-jobs.json` | Declarative job catalog (which job kinds exist and their parameters) | REFERENCE | Read for shape only: a job's definition is data in a catalog, not an ad-hoc code path. |

**Path check (GR-10 honesty).** The issue points at `docker/worker-fleet`,
`scripts/fleet` and `config/fleet-jobs.json`; all three exist. Two specifics worth
flagging rather than paraphrasing:

- The `flock -n -E 99` idiom is **not** in `docker/worker-fleet/up.sh` — the file
  the issue's wording most suggests — nor in `scripts/fleet/fleet-up-verify.sh`. It
  is in `docker/worker-fleet/fleet.cron` (lines 62, 91–92), where the skip is logged
  as `SKIPPED … previous run still holds the lock`. `up.sh` exists but carries no
  `flock`.
- `git-guard.sh` is at `docker/worker-fleet/git-guard.sh`, **not** under
  `scripts/fleet/`; `docker-api.sh` **is** at `scripts/fleet/docker-api.sh` as the
  issue says.

**Adopted as pattern, never as code.** These idioms inform this repo's own ops
tooling (a distinct skip exit, a secret mounted not specced, a credential never
clobbered, a digest pin, a build-time validation, a destructive-git refusal). They
are **not** a licence to copy the source's bash or container stack, and none of its
files are committed here.

**Cross-ref.** The container-free mechanical execution lane that consumes the same
source — `docker/worker-fleet` pool/executor/fanout — is specified separately in
[`MECHANICAL-EXECUTION-LAYER.md`](MECHANICAL-EXECUTION-LAYER.md) (issue #239).

**Provenance (GR-10):** `harvested_from:` `kushin77/leaderboard`
(`docker/worker-fleet/fleet.cron`, `scripts/fleet/fleet-up-verify.sh`,
`docker/worker-fleet/docker-compose.worker-fleet.yml`,
`docker/worker-fleet/entrypoint.sh`, `docker/worker-fleet/Dockerfile`,
`docker/worker-fleet/git-guard.sh`, `scripts/fleet/docker-api.sh`,
`config/fleet-jobs.json`) — pattern/REFERENCE only, kushin77 proprietary /
internal use only, no code copied.

## 13. paperclip.ing gap analysis (issue #368) — upstream product vs fleet primitives

Issue #368 analyses the upstream product **paperclip.ing**
(`paperclipai/paperclip`, **MIT License**, latest release seen v2026.831.1,
retrieved 2026-09-13) as a **fork-map / adoption decision**, never a vendoring.
The artifact is `docs/PAPERCLIP-ING-GAP-ANALYSIS.md`; its gate is
`scripts/check-paperclip-gap-analysis.sh` (it mutates its own input, so it cannot
pass vacuously). The document maps upstream's six capability families — org
chart, goal alignment, heartbeats, budgets & costs, tickets + audit, governance —
onto the fleet's own primitives, **by file path**, then records a
cannibalize-vs-build table.

| Source (upstream) | Asset | Verdict | Feeds |
|---|---|---|---|
| `paperclipai/paperclip` (product **paperclip.ing**) | six capability families (org chart, goal alignment, heartbeats, budgets & costs, tickets + audit, governance) + a company-scoped `/api` control-plane surface (`/api/companies/{companyId}/...`, `X-Paperclip-Run-Id`, `GET /api/openapi.json`) | REFERENCE | `docs/PAPERCLIP-ING-GAP-ANALYSIS.md` (fork-map), `scripts/check-paperclip-gap-analysis.sh`, and the follow-on integration lane (issue #370) |

**Namesake disambiguation — four distinct "paperclips".** The fleet *agent*
`paperclip` (`registry/profiles/seeds/paperclip.1.0.0.yaml`,
`registry/personas/cards/paperclip.yaml`), the gateway *provider* `paperclip`
(`gateway/providers/paperclip.py`), the vendored CMR *planning module*
`paperclip` (`vendor/CMR/catalog/modules/paperclip`) and the upstream *product*
`paperclip.ing` are four unrelated artifacts; the first two are separated by
`docs/decision-records/ADR-0012-hermes-paperclip-boundary.md`.

**License / provenance (GR-6, GR-10).** Upstream is MIT; only its public
documentation and repository surface (docs.paperclip.ing, the `paperclipai/paperclip`
repo) were read. **No upstream code was copied into this repository**, and no
upstream file is a dependency of any fleet primitive named in the analysis.

## 14. Per-repo agent fleet / CTO overlay / authority + roll-up (issue #144)

EPIC #144 generalizes the org's mature per-repo agent-fleet machinery. Every
asset below was read from a local checkout and **re-implemented for this repo**
where the mature shape was bash- or GitHub-bound; the verdict says which.

| Source (repo-relative) | Asset | Verdict | Lands in |
|---|---|---|---|
| `leaderboard/docs/CTO_OVERLAY.md` | 4-layer overlay spec (BLOCKING/WARNING, non-negotiable signals) | PATTERN | `governance/cto-overlay/` (#147) |
| `leaderboard/.cto/{config,schema,orchestrator}.yaml` | overlay config + JSON-Schema + master workflow | PATTERN | `governance/cto-overlay/schema.yaml` (#147) |
| `leaderboard/scripts/cto/{apply-overlay,config-engine,cto-orchestrator}.sh` | severity engine + the upstream `_verdict` no-false-green tally fix | PATTERN | `governance/cto-overlay/overlay.py` (#147) |
| `capital-underwriting/docs/wiki/ELITE_COMMANDER_LTC_CHARTER.md` | Commander holds the only merge authority; LTC is the execution arm | PATTERN | `governance/authority/matrix.yaml` (#150) |
| `capital-underwriting/docs/wiki/ELITE_{GENERAL,PLATOON_LEADER,AUDITOR,SCRIBE}_CHARTER.md` | chain-of-command, per-role authority, separation of duties | PATTERN | `governance/authority/model.py` (#150) |
| `leaderboard/docs/LEADERBOARD_PROTOCOL.md` | worktree-per-session isolation (collision → isolation) | PATTERN | `governance/authority/isolation.py` (#150) |
| `leaderboard/scripts/data/fleet-metrics-per-model.sh` | per-model invocation/success/failure/cost metric shapes | PATTERN | `governance/rollup/model.py` (#151) |
| `leaderboard/docker/worker-fleet/personas.yaml` + `config/platoons.yaml` | fleet composition + lens→fleet→parallelism routing | PATTERN | `control-plane/fleet-template/template.yaml` (#146) |
| `leaderboard/lib/fleet-roster.sh` | ROLE/TIER/MODEL/TRANSPORT/EFFORT roster as single source of truth | PATTERN | `control-plane/fleet-template/schema.yaml` (#146) |
| `capital-underwriting/infra/docker/worker-fleet/personas.yaml` | 30-persona fleet + definition-vs-run-state split | PATTERN | run-state model, `control-plane/fleet-template/` (#146) |
| `leaderboard/lib/fleet-roster.sh` | ROLE/TIER/MODEL/TRANSPORT/EFFORT separation + measured per-model capability JSON | PATTERN | `registry/profiles/agent-profile.schema.json`, `registry/personas/persona-card.schema.json` (#145) |
| `leaderboard/docker/worker-fleet/personas.yaml` | 15 container personas (role/description/env/memory_file/cron_lines) | READY | seeded profiles + SME cards (#145) |
| `capital-underwriting/config/leaderboard/{capability-registry,route-policy,tier-policy}.json` | agent roles + worker fleets, complexity/risk→chain+tier, model tiers with caps/fallbacks | PATTERN | registry vocabulary: roles, tool allowlists, fallback chains, model tiers (#145) |
| `capital-underwriting/scripts/agent/sme/*.txt` + `sme-dispatch.sh` | DOMAIN→SME system-prompt library (6 files) | REFERENCE | SME cards + domain mapping (#145) |
| `deepseek/config/capabilities.toml` + `docs/operations/sme-card-template.md` + `scripts/sme-card-check.py` | declared-data capability catalog + SME-card shape enforcement | PATTERN | card/profile shape (#145) |
| `vendor/CMR/onboarding/agent-profiles/role.schema.json` (+ `profiles/`, `validate.sh`) | the canonical CMR role vocabulary | READY | `registry/parity/canonical/cmr-role-vocabulary.json` frozen baseline (#145) |
| `capital-underwriting/docs/operations/{SME_SQUAD_DESIGN,TWO_PLATOON_SPLIT_DESIGN}.md` | 7-domain SME routing + platoon lane split | PATTERN | `gateway/sme-routing/policies/` (#149) |
| `capital-underwriting/config/leaderboard/capability-registry.json` | agent roles (planner/executor/verifier/critic) + worker fleets with access levels + dispatch chains | READY | `gateway/sme-routing/policies/capability-registry.yaml` (#149) |
| `capital-underwriting/config/leaderboard/route-policy.json` | complexity/risk → chain + tier (fast/deep/strict), keyword thresholds | READY | `gateway/sme-routing/policies/route-policy.yaml` (#149) |
| `capital-underwriting/config/leaderboard/tier-policy.json` | model tiers (flash/pro/auditor) + timeouts, token caps, fallbacks, complexity→tier | READY | `gateway/sme-routing/policies/tier-policy.yaml` (#149) |
| `capital-underwriting/config/agent-module-authority.json` | domain→module authority matrix | REFERENCE | derived domain→module edges (#149) |
| `vendor/CMR/catalog/schemas/gdc-manifest.schema.json` | GDC framing, `owner/name` identity | REFERENCE | `governance/rollup/schema.yaml` (#151) |
| `vendor/CMR/catalog/topology/topology.json` | drift-flagged edges | REFERENCE | `governance/rollup/model.py` (#151) |
| `vendor/CMR/onboarding/agent-profiles/role.schema.json` | the closed SME `role` enum used by the pilot tenants | REFERENCE | `governance/rollup/pilot/` (#151) |

**Declared-but-absent upstream assets.** The file names the issues cite are
**not present** in the populated `vendor/CMR` submodule:
`catalog/sme-registry.tsv` and `onboarding/agent-profiles/persona-registry.json`
do not exist, and `grep -rn weekly_spend_ceiling vendor/CMR` returns **zero**
matches. The per-SME `weekly_spend_ceiling` primitive is therefore **defined by
this repo** (`governance/rollup/`), not harvested — recorded here so a later
reader does not hunt for a source that is absent. (Provenance is GR-10: repo,
path, verdict; see also the PROVENANCE tables in `docs/CTO-OVERLAY.md`,
`docs/AUTHORITY-MODEL.md` and `docs/ENTERPRISE-ROLLUP.md`.)

**A second declared-but-absent source.** `capital-underwriting/infra/docker/worker-fleet/fleet-roster.conf`
is cited by issue #146 and **does not exist** in the local checkout (that
directory holds `personas.yaml`, `fleet.cron`, `docker-compose.worker-fleet.yml`,
`entrypoint.sh`, …). It is recorded as `NOT FOUND` in the #146 PROVENANCE table
rather than silently substituted.

**Third and fourth declared-but-absent sources (issue #145).**
`vendor/CMR/catalog/sme-registry.tsv` — the file credited with the
`weekly_spend_ceiling` primitive — **does not exist**, and
`grep -rn weekly_spend_ceiling vendor/CMR` returns **0 matches**; the field is
therefore **defined by this repo** (`registry/profiles/agent-profile.schema.json`,
`minimum: 0`) and documented as such. Also absent:
`capital-underwriting/.claude/agents/{gcp-gatekeeper-sme,ltc-brain-fleet-sre,qa-playwright-sme}.md`
(the directory holds only `session.json`s). Per-asset detail:
`docs/REGISTRY-PROVENANCE.md`.

---

## 15. shared-services SSH-over-Cloudflare-Tunnel route (issue #771) — PATTERN, ported

| Field | Value |
|---|---|
| Source repo | `kushin77/shared-services` |
| Source path | `scripts/deploy-ssh-tunnel-access.sh` (247 lines) |
| License | **none declared** — the repository is **private** and carries no licence file |
| Owner | the **same owner** as this repo (a first-party estate, not a third party) |
| Verdict | **PATTERN** — the *shape* is the asset; the implementation is tied to that estate |
| Ported to | `infra/cloudflare/ingress.py` + `infra/cloudflare/ao-ssh-access.sh`, gated by `scripts/check-ao-ssh-access.sh` |
| Register | `surfaces.remote_ssh_access` in `infra/feature-flags/registry.yaml`, ships **OFF** (GR-5) |

**The pattern.** A hostname is published through an *existing* remotely-managed
Cloudflare Tunnel by merging one `ssh://<origin>:22` rule into the tunnel's live
`config.ingress` and writing the merged array back — **never** a blind
replacement, which would delete the tunnel's other live rules. Then a proxied
CNAME to `<tunnel id>.cfargotunnel.com`, then a Cloudflare Access self-hosted
application plus an allow-policy so the hostname is not an unauthenticated public
door to sshd, then a verification pass.

**No code was copied.** The source is a private, unlicensed repository owned by
the same owner, so nothing in it may be copied verbatim. The pattern was
reimplemented here, in this repo's conventions: its own comments, its own
refusal semantics (a named refusal per missing identifier rather than a defaulted
value), its own pure-function split, its own tests and its own gate. No
paragraph, comment or identifier was carried across, and nothing was vendored:
`vendor/` is untouched, no submodule was added and no source file was cloned into
the tree.

**What we changed, and why.**

| Upstream | Here | Why |
|---|---|---|
| `CF_ACCOUNT_ID` / `CF_ZONE_ID` / the tunnel id / the access emails default to the estate's own values | every identifier and the email allow-list come from the **environment**, and a missing one is **refused by name** | a default in this repo would point the run at another estate; the upstream values are that estate's and are not in this tree at all |
| Vault, then GSM, then env for the API token | env (`CF_API_TOKEN`) or GCP Secret Manager (`AO_CF_TOKEN_SECRET` + `AO_GCP_SECRET_PROJECT`); never a file, never git | GR-6, and this repo has no Vault dependency to borrow |
| apply by default | **dry run is the default**; `--apply` mutates and refuses while the surface flag is OFF | GR-5: new infrastructure ships OFF and is promoted by a reviewed change, not by a default |
| the merge lived in a shell heredoc | the merge is a **pure function** with its own suite, and the gate **mutation-proves** it against a neutered copy | one definition, unit-testable, and a gate that can genuinely fail |
| Access could be skipped (`--no-access`) | Access is **always** ensured | a hostname without an Access app is an unauthenticated public door to sshd, which is not a supported posture |
| run directly by whoever held the token | the live apply is documented as an **operator act**, and the route ships OFF | it changes an estate this repo does not own |

**See also.** `docs/OPERATOR-ACCESS.md` §6 documents the route end to end,
including why the hostname is useless without the Access app and why a service
token is what makes it headless.

---
*End of index. Raw evidence: `.research/reports/` (24 reports, gitignored).*
