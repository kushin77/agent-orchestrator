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
*End of index. Raw evidence: `.research/reports/` (24 reports, gitignored).*
