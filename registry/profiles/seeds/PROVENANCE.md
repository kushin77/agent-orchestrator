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

Cross-cutting field semantics (all seeds):

| Vocabulary axis | Derived from |
|---|---|
| model tiers LOW/MED/HIGH/MAX (flash/pro) | `kushin77/CMR` `onboarding/agent-profiles/role.schema.json` (docs/MODEL-PROFILES.md ladder) |
| memory scopes user/session/repository | `kushin77/vscode-memory` `scripts/memory_system_v2.py` (MemoryType USER/SESSION/REPOSITORY) |
| versioned prompt ref `<module>/<name>@v<n>` | `kushin77/gmail-agent` `src/agent/prompts/<task>/v1.ts` (VERSION = '<task>/v1' + OutputSchema) |
| snake_case tool ids | `kushin77/gmail-agent` `src/agent/tools.ts` (get_gmail_thread, create_calendar_event, search_memory, ...) |
| guardrail/constraint ids | `kushin77/agent-orchestrator` `AGENTS.md` hard DON'Ts + `docs/GOLDEN-RULES.md` (GR-4/5/6/12, AO-GR-1/2/3/6) |
| agent_class reviewer/orchestrator/investigator | `kushin77/shared-governance` `GLOBAL_STANDARDS/schemas/agent-task.schema.json` |
