# Spike #48 — Deep-extract elevatediq monorepo orchestration assets

> **Type:** research spike (recon only — no product code).
> **Status:** complete.
> **Harvest date:** 2026-09-08.
> **Issue:** [kushin77/agent-orchestrator#48](https://github.com/kushin77/agent-orchestrator/issues/48) (work item 44, phase 1).
> **Lane:** issue-48-spike-elevatediq — owns this file only.
> **Recon method:** read-only inspection of the local radar tree map
> `.research/trees/elevatediq.tree.txt` (targeted sections) + targeted read-only
> `gh api` content fetches from `kushin77/elevatedIQ@develop` (default branch,
> verified 2026-09-08) for the issue's named candidates. No full clone of the
> ~8.8 GB monorepo and no whole-monorepo recursive `git/trees` call was issued.

## TL;DR

The elevatediq monorepo (`kushin77/elevatedIQ`) holds several **live, unique
orchestration/agent/guardrail assets that the 24-repo harvest never touched** —
the harvest fed off dissected standalone repos, and the monorepo keeps the
originals plus assets that were never dissected. The standout finds for
EPIC-00 are the `services/ai-chatbot-orchestrator` (a compact multi-provider
**model-gateway + agent-tool-orchestrator reference**: local-first router,
per-tenant rate limits, RBAC, PII redaction, Docker/Firecracker tool sandbox
with per-category security profiles, MCP tool registry) and the
`config/finops/finops.yml` + `services/finops-engine` pair (a complete
declarative FinOps schema + engine with per-tenant attribution, showback /
chargeback, anomaly detection and forecasting). Most candidates **map onto
already-open backlog issues** (gateway, FinOps, memory, MCP, DLP, orchestration)
so they are recorded here as **inputs/patterns to those issues, not new board
items**; exactly **one** genuinely non-represented asset (agent tool-execution
sandboxing) was opened as a follow-up feature issue (#58).

## 1. Asset table — unique orchestration / agent / guardrail assets in elevatediq

Repo prefix = `kushin77/elevatedIQ@develop`. Verdicts use the CANNIBALIZATION.md
legend (READY / PATTERN / REFERENCE).

| # | Asset (`repo:path`) | One-line purpose | Verdict | Recon method | Superseded / already represented? |
|---|---|---|---|---|---|
| 1 | `services/ai-chatbot-orchestrator/` (Go, ~40 source files) | Multi-provider AI chat orchestrator: local-first model router (Ollama→Claude→OpenAI w/ priority + fallback), Redis context mgmt (TTL/token/compression), per-tenant quota + rate limit, cost-per-query + budget, RBAC (4 roles) + audit, PII redaction, Docker + Firecracker tool sandbox, MCP tool registry, k8s client, SSE streaming, learning pipeline, AI coverage-scanner | PATTERN (per-module; see rows 1a–1d) | Tree map lines 18821–19042 (non-vendor) + raw fetches: `README.md`, `internal/orchestrator/orchestrator.go`, `internal/mcp/registry.go`, `internal/sandbox/profiles.go`, `internal/pii/detector.go`, `internal/firecracker/client.go`, `internal/models/model.go` | Not harvested (no dissected repo). Maps onto gateway pillar (#15/#16/#17), guardrails (#20/#26/#27/#30), #25 memory, #12 RBAC — see per-row |
| 1a | `services/ai-chatbot-orchestrator/internal/models/{model,ollama,claude,openai}.go` + `orchestrator/orchestrator.go` | Model interface + 3 provider adapters; orchestrator registers models w/ priority, routes + falls back, returns cost/latency/tokens per query | PATTERN | Raw fetch `orchestrator.go` + `models/model.go` | Superseded in shape by #11 (multi-provider adapters) + #12 (gateway proxy) + #17 (FinOps chooser) — read as reference impl |
| 1b | `services/ai-chatbot-orchestrator/internal/rbac/{rbac,audit}.go` | 4-role RBAC + tool-permission gating + audit log on tool execution | READY (small, portable) | Tree map + `internal/mcp/registry.go` fetch (shows gating) | **Superseded** — saas-rbac is the canonical RBAC (harvested READY); product RBAC already in #12 + #36/#37. Do not adopt |
| 1c | `services/ai-chatbot-orchestrator/internal/pii/detector.go` | Regex PII detector/redactor: 10 PII types (email/phone/SSN/CC/IP/DOB/address/name/URL/API-key) w/ per-type enable + confidence | READY (drop-in regex set) | Raw fetch `pii/detector.go` | Input to #27 (DLP); overlaps ollama `dlp_redactor` (GCP-DLP) + shared-governance scrub-rules — adopt as fallback rule set, no new issue |
| 1d | `services/ai-chatbot-orchestrator/internal/sandbox/{profiles,docker}.go` + `internal/firecracker/*` | Tool-execution sandbox: 3 security profiles (restricted/standard/privileged: read-only rootfs, no-new-privs, cap-drop, network none/bridge/host, CPU/mem/PID/timeout) + `CategoryProfiles` map (file/exec/network/db/cloud/git/docker/k8s → profile, fail-closed) + Docker isolation + Firecracker-go-sdk microVM lifecycle | PATTERN (profiles + category map near-READY; microVM executor REFERENCE) | Tree map (client.go, vm.go, network.go, snapshot.go) + raw fetch `profiles.go` + `firecracker/client.go` | **NOT represented in backlog or harvest** — opened follow-up **#58** (agent tool-execution sandboxing) |
| 1e | `services/ai-chatbot-orchestrator/internal/mcp/{registry,handler,tools_*}.go` | MCP tool registry: categories file/exec/network/db/cloud/git/docker/k8s, RBAC-gated Execute, audit | PATTERN | Raw fetch `mcp/registry.go` | Superseded by #20 (tenant-scoped MCP tool gateway) + harvested mcp-hub / code-indexing MCP catalog — the sandbox-profile hook (1d) is the novel part |
| 2 | `pkg/automationorchestrator/workflow_engine.go` (+ `orchestrator.go`, `workflow_test.go`) | Prompt-chain DAG orchestration engine: YAML `WorkflowDefinition`; step types prompt/branch/loop/aggregate; variable refs `${step.output}`; per-step retry/error-handler/timeout; async goroutine executor; `ExecutionResult` + audit path; consumes `pkg/promptgateway` | PATTERN (prompt-step vocabulary unique; engine in-memory, non-durable) | Tree map 16096–16098 + raw fetch `workflow_engine.go` (23 KB) + `orchestrator.go` (build-ignored reference) | Shape superseded for durability by #21 (Temporal) + leaderboard actor-loop harvest; **read the prompt-step type vocabulary when implementing phase-3 #22/#23** |
| 3 | `config/finops/finops.yml` + `services/finops-engine/{engine.py,api.py}` | Declarative FinOps schema (tag taxonomy incl. `tenant`, cost centers + budgets, GCP/AWS/on-prem, optimization rules, budget alerts + throttle action, anomaly detection, showback/chargeback allocation + markup, forecast scenarios, Prometheus metrics) paired with a 38 KB Python engine (per-tenant cost attribution, sklearn ML anomaly + forecasters, budget enforcement, chargeback/showback reports, BigQuery/budgets integration) | PATTERN / REFERENCE (strongest FinOps find) | Tree map 27229–27236 + raw fetch `finops.yml` (10 KB) + `engine.py` (38 KB) + `api.py` | Input to #33 (usage metering + cost engine) + #34 (per-tenant budgets) — richer single-package than the fragment FinOps assets in CMR/leaderboard/monitoring-stack/capital-underwriting/shared-services. No new issue (already represented) |
| 4 | `config/agent-autonomy.yml` | Declarative agent-autonomy policy: scheduling, `dry_run_default`, `require_label_for_live`, rate limits, canary rollout %, action circuit-breaker, `scope: allowed/conditional/denied` action allowlists, remediation categories, metrics, notifications | REFERENCE (compact, adoptable policy schema) | Raw fetch (1.4 KB) | Maps to #26 (policy-as-code gates) + #23 (egress) + phase-8 canary doctrine — read when designing agent org "boundaries". No new issue |
| 5 | `pkg/defrag/orchestrator.go` | FANG workflow orchestrator: analyze→plan→validate→execute→rollback with audit events + metrics | PATTERN (domain-specific) | Raw fetch (7 KB) | **Superseded** by defragsuite harvest (defrag domain already READY/PATTERN in CANNIBALIZATION §2). Do not duplicate |
| 6 | `services/memory-service/` (Python/FastAPI) | Agent memory service: `/store` + `/retrieve` of text+embedding; pgvector or sqlite + FAISS; cosine sim; pgvector migration | PATTERN (complementary to vscode-memory harvest) | Tree map 27426–27439 + raw fetch `main.py` | **Represented** by #25 (scoped agent memory store) — input, no new issue |
| 7 | `services/model-registry/` (Go) | Model registry CRUD server (handlers/model/store) | REFERENCE (small) | Tree map 27449–27462 | Superseded by #10 (agent registry) + #40 (agent-pack registry) — no new issue |
| 8 | `services/prompt-library/` | Categorized prompt library (ai/architecture/code-review/defrag/devops/qa/security), YAML templates, `schema.json`, CLI, web, RAG index, Ollama Modelfiles | REFERENCE | Tree map 27707–27762 | **Superseded** by agent-orchestrator's own versioned prompt module library (#13, shipped on master) + code-indexing catalog. Do not duplicate |
| 9 | `.github/prompts/ai/*` (3 files) | Agent-orchestration prompt templates w/ structured JSON output + guardrail rules (defrag-ai-orchestration, ollama-model-selection, ollama-integration-example) | REFERENCE | Tree map + raw fetch `defrag-ai-orchestration.md` | Superseded by #13 prompt modules + #38 instruction layer. Low value |
| 10 | `services/agent-autopilot/` (Python) | Sidecar agent: continuous drift-analysis + (dry-run) remediation loop w/ guardrails (maintenance window, cooldown, auto-PR toggle), Prometheus metrics | PATTERN | Tree map 18766–18770 + raw fetch `main.py` (13 KB) + `README.md` | Superseded in shape by leaderboard loop-daemons harvest (richer) — no new issue |
| 11 | `services/ai-observability-engine/` | ML-observability engine: drift detection (evidently), explainability (SHAP/LIME/feature-importance), MLflow, retraining orchestrator | REFERENCE | Tree map 26223–26259 + raw fetch `README.md` | Marginal input to #18 (model-health monitoring — LLM not ML-drift). No new issue |
| 12 | `docs/rca-orchestrator/*` + `docs/architecture/*` (MICROSERVICES_ORCHESTRATION, AI_CHATBOT_ARCHITECTURE) | RCA/orchestration architecture + roadmap docs (6-layer design, 4-week plans) | REFERENCE | Tree map + raw fetch `rca-orchestrator/README.md` | Superseded by git-rca-workspace harvest + EPIC-00's own `docs/ARCHITECTURE.md`. Do not duplicate |
| 13 | `services/ai-agents/`, `services/aiops-engine/` (Go originals) | Operational Go agents (devops-engineer/security-analyst/automation-expert/oracle) + AIOps engine — originals of the dissection stubs | — (already catalogued) | Tree map + cross-ref #49 | **Already surfaced** by spike #49 recovery report + CANNIBALIZATION §4 — do NOT duplicate here |
| 14 | `operations/scripts/**`, `infrastructure/**/aiops` | ~2k ops shell scripts + k8s aiops overlays | REFERENCE (spot) | Tree map scan | Bash-fleet logic superseded by leaderboard (675-script harvest); aiops overlay superseded by #49 aiops-engine pointer. Low value |

## 2. What is better than what CMR / leaderboard already harvested

1. **FinOps as one package (row 3).** CMR contributes `cost-report.sh`, leaderboard
   `finops-budget.yaml` + token-counter + cost attribution, monitoring-stack
   chargeback/SLO scripts, capital-underwriting `aiUsage.ts`, shared-services
   `budget_enforcer` — all fragments. elevatediq's `finops.yml` **schema +
   `engine.py`** combine per-tenant tag allocation, showback/chargeback
   methodology + markup, ML anomaly detection, budget enforcement with
   `throttle_requests` action and multi-scenario forecasting in a single
   declarative unit. Closest thing to a ready FinOps subsystem for #33/#34.
2. **Tool sandbox profiles (row 1d).** leaderboard's guards are process-level
   (`guard.sh`, fanout locks); CMR gates are policy toggles. Nothing in the
   harvest expresses **tool-category → container/microVM security profile**
   (resource caps, cap-drop, network mode, read-only rootfs, fail-closed
   default). This is the unique, adoptable difference.
3. **Prompt-chain DAG vocabulary (row 2).** git-rca-workspace's
   `workflow_engine.py` is a templated multi-step engine and shared-temporal
   adds durable infra, but neither models **LLM prompt steps with
   branch/loop/aggregate step types and `${step.output}` variable wiring** —
   the exact vocabulary the product's phase-3 agent pipelines need.
4. **Agent-autonomy contract (row 4).** `agent-autonomy.yml` is a compact,
   single-file **agent-boundary policy** (allowed/conditional/denied action
   allowlists + canary + circuit breaker + dry-run default) that is more
   directly reusable than the distributed policy fragments in shared-governance.

## 3. Follow-up feature issues opened (deduped against backlog + CANNIBALIZATION.md)

| Issue | Title | Labels | Rationale |
|---|---|---|---|
| **#58** | Sandboxed agent tool execution (Docker/Firecracker isolation + per-category security profiles) | `type:feature`, `priority:P2`, `phase:4-guardrails-security`, `pillar:guardrails-security`, `source:cannibalized` | Row 1d is genuinely worth adopting and **not represented** by any backlog guardrail issue (DLP/egress #27, policy #26, honesty #28, tenant isolation #30 are all distinct surfaces) nor by CMR/leaderboard harvests. |

No other new issues were opened: rows 1a–1c, 1e, 2, 3, 4, 6, 7 all map onto
already-open backlog items (#10/#11/#12/#13/#16/#17/#20/#21–25/#26/#27/#33/#34/
#38/#40) and are recorded above as **inputs/patterns**, not new board work;
rows 5, 8, 12, 13, 14 are already superseded by existing harvests or prior
spikes and are flagged do-not-duplicate.

## 4. Provenance & recon log (read-only, 2026-09-08)

- Tree-map sections used: `services/*` (105 top-level services; focus
  ai-chatbot-orchestrator, ai-agents, aiops-engine, finops-engine,
  memory-service, model-registry, prompt-library, agent-autopilot,
  ai-observability-engine), `pkg/{automationorchestrator,promptgateway,defrag}`,
  `config/{finops,agent-autonomy,prompts}`.
- Targeted `gh api` content fetches (`repos/kushin77/elevatedIQ/contents/<path>?ref=develop`,
  base64-decoded): the 16 files listed in §1 rows above; all returned content
  (no 404s) → access to the private repo is already granted to the kushin77
  token (same conclusion as #49).
- Cross-check sources read to dedup: `docs/CANNIBALIZATION.md` (master),
  harvest reports under `.research/reports/{CMR-harvest.md,
  leaderboard-harvest.md, fleet/*-harvest.md}`, spike #49 report
  (`docs/spikes/49-eiq-ai-recovery.md`), and the open-issue backlog
  (`gh api repos/kushin77/agent-orchestrator/issues?state=open`).
- Worktree `ao-wt-48` (branch `issue-48-spike-elevatediq`) left in place; raw
  recon evidence under `$TMPDIR/recon-48/` (not committed).
