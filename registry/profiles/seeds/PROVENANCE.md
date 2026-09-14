# Seed Profile Provenance

Every seed profile under `registry/profiles/seeds/` is **derived from** (not
copied from) the harvested fleet/hub assets below, per the repo provenance
doctrine (GR-10; `docs/CANNIBALIZATION.md`, issue #8). Field vocabulary, tiers
and memory-scope semantics are normalized to `registry/profiles/catalog.yaml`.

| Seed (`id`) | Derived from (repo, path) | Fields informed |
|---|---|---|
| `orchestrator` | `kushin77/leaderboard` `docker/worker-fleet/personas.yaml` (orchestrator persona) | capabilities (orchestrate, task-claim), constraints (issue-first, verify-before-done), tools (board_sync, gh_issue) |
| `orchestrator` | `kushin77/shared-governance` `GLOBAL_STANDARDS/schemas/agent-task.schema.json` (agent_class=orchestrator) | capability `orchestrate`, plan/dispatch posture |
| `orchestrator` | `kushin77/CMR` `onboarding/agent-profiles/profiles/architecture-sme.json` | planner/decompose-into-parallel-lanes posture, MED tier, memory scope `user`+`repository` |
| `coder` | `kushin77/hermes-agents` `src/hermes_agent/models/capability_registry.py` + `services/capability_registry.py` (code-gen, refactor, test-gen) | capabilities code-author/test-author/test-run; capability-tagged routing pattern |
| `coder` | `kushin77/leaderboard` `docker/worker-fleet/personas.yaml` (executor, ds-worker-*) | LOW flash default worker tier, tools (file_write, shell_exec), constraints (no-unverified-merge, no-debug-leftovers) |
| `coder` | `kushin77/CMR` `onboarding/agent-profiles/profiles/general.json` | LOW/flash default, worker mode |
| `reviewer` (SME-lens) | `kushin77/CMR` `onboarding/agent-profiles/profiles/qa-sme.json` + `security-sme.json` | SME-lens review persona; evidence-carrying gates; reviewer-bundle guardrails |
| `reviewer` | `kushin77/shared-governance` `GLOBAL_STANDARDS/schemas/agent-task.schema.json` (agent_class=reviewer) | capability code-review |
| `reviewer` | `kushin77/capital-underwriting` `infra/docker/worker-fleet/personas.yaml` (auditor, continuous-audit) | capabilities audit/test-run; independent-verification posture |
| `researcher` | `kushin77/shared-governance` `GLOBAL_STANDARDS/schemas/agent-task.schema.json` (agent_class=investigator) | capability research |
| `researcher` | `kushin77/capital-underwriting` `infra/docker/worker-fleet/personas.yaml` (ds-worker-debugging) | investigation/RCA posture, tool sql_query |
| `researcher` | `kushin77/gmail-agent` `src/agent/tools.ts` (search_memory, store_memory) | memory tooling for evidence capture |
| `data-agent` | `kushin77/capital-underwriting` `infra/docker/worker-fleet/personas.yaml` (ds-worker-* pool) | capability data-analysis; tools sql_query/file_write; MED flash tier |
| `data-agent` | `kushin77/hermes-agents` `src/hermes_agent/models/capability_registry.py` | capability-tagged task routing into data-analysis |
| `ollama` | `kushin77/ollama` `ollama/services/inference/resilient_ollama_client.py` + `ollama/services/resilience/circuit_breaker.py` | local-LLM client + circuit breaker; LOW flash tier; local-inference posture |
| `paperclip` | `kushin77/llm-triage` `src/llm_triage/classifier.py` + `kushin77/gov-ai-scout` `backend/src/services/ai-provider.ts` | provider-neutral classifier + typed structured outputs; research/docs-authoring |
| `hermes` | `kushin77/hermes-agents` `src/hermes_agent/services/capability_registry.py` + `models/model_tiering.py` | code-gen/refactor/test-gen routing; tier/ceiling/cost model; MED tier |
| `deepseek` | `kushin77/leaderboard` `lib/fleet-roster.sh` + `config/deepseek-capabilities.txt` + `kushin77/capital-underwriting` `personas.yaml` (ds-worker-*) | role→tier→model chooser; DeepSeek capability catalog; data-analysis/research; MED tier |
| `claude` | `kushin77/gmail-agent` `src/agent/claude.ts` + `kushin77/CMR` `onboarding/agent-profiles/profiles/architecture-sme.json` | governed Claude client (tiers/p-retry/tool loop); orchestration posture; MED tier |
| `finops-steward` | `kushin77/leaderboard` `lib/fleet-roster.sh` (`role_capabilities()` cost_tier / cost_per_mtok) + `kushin77/capital-underwriting` `config/leaderboard/{tier-policy,capability-registry}.json` (complexity->tier->model with fallback chains; per-role tool allowlists) | FinOps metering posture; `fallbackChain`; the first-class `weekly_spend_ceiling` (issue #145) |

Cross-cutting field semantics (all seeds):

| Vocabulary axis | Derived from |
|---|---|
| model tiers LOW/MED/HIGH/MAX (flash/pro) | `kushin77/CMR` `onboarding/agent-profiles/role.schema.json` (docs/MODEL-PROFILES.md ladder) |
| memory scopes user/session/repository | `kushin77/vscode-memory` `scripts/memory_system_v2.py` (MemoryType USER/SESSION/REPOSITORY) |
| versioned prompt ref `<module>/<name>@v<n>` | `kushin77/gmail-agent` `src/agent/prompts/<task>/v1.ts` (VERSION = '<task>/v1' + OutputSchema) |
| snake_case tool ids | `kushin77/gmail-agent` `src/agent/tools.ts` (get_gmail_thread, create_calendar_event, search_memory, ...) |
| guardrail/constraint ids | `kushin77/agent-orchestrator` `AGENTS.md` hard DON'Ts + `docs/GOLDEN-RULES.md` (GR-4/5/6/12, AO-GR-1/2/3/6) |
| agent_class reviewer/orchestrator/investigator | `kushin77/shared-governance` `GLOBAL_STANDARDS/schemas/agent-task.schema.json` |

## Backfilled schema vocabulary (issue #145)

The profile schema gained the ROLE/TIER/MODEL/TRANSPORT/EFFORT separation plus
tool-allowlist/fallback-chain/model-tier fields, harvested from:

| New schema axis | Derived from | Notes |
|---|---|---|
| `role` (12 canonical ids) | `kushin77/CMR` `onboarding/agent-profiles/role.schema.json` (`properties.role.enum`) | mirrored exactly; `registry/parity` fails if it drifts |
| `model` (flash/pro) | same schema (`model.model.enum`) | separates MODEL from `defaultModelTier` |
| `transport` (deepseek/session/api) | `kushin77/leaderboard` `lib/fleet-roster.sh` `role_transport()` | not a CMR axis (documented) |
| `effort` (low/medium/high) | `kushin77/leaderboard` `lib/fleet-roster.sh` `role_effort()` | not a CMR axis (documented) |
| `capabilityTier` (deep/balanced/fast) | `kushin77/leaderboard` `lib/fleet-roster.sh` `role_capability_tier()` | not a CMR axis (documented) |
| `fallbackChain` (role-id list) | `kushin77/capital-underwriting` `config/leaderboard/capability-registry.json` (`fallback`) | every id must be a canonical CMR role |
| `canonicalLanes` (17 canonical ids) | `kushin77/CMR` role schema (`ownedLanes.items.enum`) | mirrored exactly; parity-checked |
| `weekly_spend_ceiling` (number >= 0) | **defined here** — the declared CMR source `catalog/sme-registry.tsv` is absent | see `docs/REGISTRY-PROVENANCE.md` |

All new fields are **optional with a documented default** (absence = the platform
budget guardrail governs), so the existing published seeds and their sha256
ledger entries are unchanged.

## Persona SME-card provenance (issue #145)

The five SME cards added by issue #145 carry their own `provenance` field:

| Card | Derived from |
|---|---|
| `platform-sme` | `kushin77/CMR` `onboarding/agent-profiles/profiles/platform-sme.json` + role schema |
| `pmo-sme` | `kushin77/CMR` `onboarding/agent-profiles/profiles/pmo-sme.json` + leaderboard PMO personas |
| `sync-sme` | `kushin77/CMR` `onboarding/agent-profiles/profiles/sync-sme.json` + leaderboard `sync-daemon` persona |
| `gcp-gatekeeper-sme` | `kushin77/capital-underwriting` `docs/strategy/gcp-gatekeeper-p0-roadmap.md` + CMR `iac-sme` card (the declared `.claude/agents/gcp-gatekeeper-sme.md` is absent) |
| `mechanical-sme` | `kushin77/deepseek` `docs/operations/sme-card-template.md` + `scripts/sme-card-check.py` + leaderboard fleet-roster |

The full repo/path/license/verdict table, including the sources that were
verified **absent**, is in `docs/REGISTRY-PROVENANCE.md`.

## Parity gate (issue #145)

`registry/parity/` compares the registry's declared vocabulary against the
canonical CMR catalog and is tri-state (0 OK / 1 NOT-OK / 2 CANNOT-ASSESS). See
`registry/parity/README.md` for the direction contract.
