# Spike #49 — Recovering eiq-ai upstream for stub agent repos

> **Type:** research spike (recon only — no product code).
> **Status:** complete.
> **Harvest date:** 2026-09-08.
> **Issue:** [kushin77/agent-orchestrator#49](https://github.com/kushin77/agent-orchestrator/issues/49) (work item 45, phase 0).
> **Lane:** issue-49-spike-eiq-ai-recovery — owns this file only.
> **Recon method:** read-only GitHub API (`gh api`) + local tree maps
> (`.research/trees/elevatediq.tree.txt`, per-repo `*.tree.txt`,
> `.research/trees/repos.txt`) + read-only inspection of the stub clones under
> `.research/fleet/`. No monorepo was full-cloned and no whole-monorepo
> recursive `git/trees` call was issued.

## TL;DR

Every stub has a live, readable upstream home in the (private) ElevatedIQ
orgs. **Read access is already granted** to the kushin77 token for all of them
(verified by successful API reads), so none requires a new grant, fork, or
subtree to cannibalize from. Two stubs (`intelligence`, `issue-aggregator`)
carry **real but partial** code whose original sibling modules
(`smart_selector`/`llm_auto_triage`/`semantic_clustering`;
`models`/`schema`/`connectors`) are **not recoverable from any accessible
repo** — they were lost during Phase-2 dissection — but the upstream monorepo
now hosts **self-contained refactored replacements** that are the correct
recovery target. `llm-triage` is the already-good contrast (no recovery
needed).

## 1. Local stub inventory (source of the module references)

All stubs are clones of `kushin77/*` standalone repos (origin URLs read from
each stub's `.git/config`), created by "Phase-2 dissection" of the elevatediq
monorepo. Contents inspected read-only:

| Local stub (`.research/fleet/`) | Origin (git remote) | Files | What its code references |
|---|---|---|---|
| `ai-agents` | `kushin77/ai-agents` | README, MIGRATION_NOTES, VALIDATION (doc-only) | README claims "composable AI agent framework", Python, migrated to `elevatediq-ai/eiq-ai/packages/ai-agents` |
| `aiops-engine` | `kushin77/aiops-engine` | README, MIGRATION_NOTES, VALIDATION (doc-only) | README claims migrated to `elevatediq-ai/eiq-ai/packages/aiops-engine` |
| `intelligence` | `kushin77/intelligence` | README (empty), `ensemble_scorer.py`, `prompt_tuner.py` | `ensemble_scorer.py` imports `.smart_selector` (`SmartSelector, ScoringResult`), `.llm_auto_triage` (`LLMAutoTriage`), `.semantic_clustering` (`SemanticClustering`) — **missing** |
| `issue-aggregator` | `kushin77/issue-aggregator` | README, `deduplication.py`, `main.py`, Dockerfile, requirements.txt | `main.py` + `deduplication.py` import `models` (`NormalizedIssue`, …), `schema` (`Issue`), `connectors.github` (`GitHubConnector`), `connectors.gitlab` (`GitLabConnector`) — **missing** |
| `llm-triage` (contrast) | `kushin77/llm-triage` | README + `src/llm_triage/{__init__,classifier,feedback,few_shot,integration}.py` | complete 4-module package — no recovery needed |

## 2. Upstream repo inventory (verified read-only, 2026-09-08)

| Repo | Visibility | Default branch | Size | Last push | Role |
|---|---|---|---|---|---|
| `elevatediq-ai/eiq-ai` | private | `main` | 35 KB | 2026-03-29 | Declared "new home" monorepo; **packages are mostly doc stubs** — only `packages/llm-triage` holds real code |
| `elevatediq-ai/ElevatedIQ-Mono-Repo` | private | `main` | ~400 MB | 2026-06-16 | Earlier consolidation; holds migrated service apps (`apps/aiops-engine`, `apps/agent_framework`) |
| `elevatediq-ai/eiq-org` | private | `main` | ~730 MB | **2026-09-01** | **Current, most-active mega-monorepo**; nests many org repos under `apps/` and hosts the refactored `shared/automation/*` copies |
| `kushin77/elevatedIQ` | private | `develop` | ~8.8 GB | 2026-08-07 | Original giant monorepo (map: `.research/trees/elevatediq.tree.txt`); still carries original `services/ai-agents` + `services/aiops-engine` |
| `kushin77/GCP-landing-zone` | private | `main` | ~228 MB | 2026-08-07 | Holds **incomplete** historical copies under `examples/` (same missing modules as the stubs) |
| `kushin77/uims-spoke` | **public** | `feat/uims-spoke/initial` | 100 KB | 2026-01-22 | Holds **evolved** `smart_selector/` + `semantic_clustering/` modules (different API, useful analog only) |

All private repos were **readable** with the current kushin77 token (contents /
trees / code-search API calls all returned data), so access is **granted** for
cannibalization. `elevatediq-ai/eiq-ai` is **not** where the real ai-agents /
aiops-engine code lives — its `packages/ai-agents` and `packages/aiops-engine`
contain only `README.md` / `MIGRATION_NOTES.md` / `VALIDATION.md` (the eiq-ai
blob list is 29 files total).

## 3. Recovery-pointer list (upstream repo:path → local stub)

### 3.1 `ai-agents` (doc-only stub)
- **Declared home (per stub README):** `elevatediq-ai/eiq-ai:packages/ai-agents/` → doc-only (not the real code).
- **Original source (per eiq-ai's own `packages/ai-agents/README.md`):**
  **`kushin77/elevatedIQ:services/ai-agents/`** (`develop`) — **REAL code**,
  live-verified. Content: Go operational agents
  (`devops-engineer/main.go`, `security-analyst/main.go`, `automation-expert/`,
  `oracle/rca-prompt-profile.json`) + `training/ollama/` (Modelfile, dataset,
  playbooks) + `tests/test_health.py`, `Makefile`, `go.mod`, `Dockerfile*`.
- **Related Python framework (not confirmed = the stub):**
  `elevatediq-ai/ElevatedIQ-Mono-Repo:apps/agent_framework/` — FastAPI
  "ElevatedIQ Agent Framework" (`main.py`, `swarm.py`,
  `agent_framework/{core.py,swarm.py}`). Candidate for the "composable agent
  framework" name but its API/purpose differs.
- **Discrepancy to resolve (owner):** stub README says "Language: Python",
  but the located original `services/ai-agents` is predominantly **Go**
  (Go modules + Ollama training, one Python health test). The label may be
  aspirational or refer to the related Python framework; see §5.
- **Pointer for future issues:** recover from
  `kushin77/elevatedIQ:services/ai-agents/` (original, most recent push) and
  cross-check `ElevatedIQ-Mono-Repo:apps/agent_framework/` for the Python
  framework shape.

### 3.2 `aiops-engine` (doc-only stub)
- **Original (live-verified):** **`kushin77/elevatedIQ:services/aiops-engine/`**
  (`develop`) — `api.py`, `engine.py`, `requirements.txt`, `Makefile`,
  `Dockerfile`, `docker-compose.yml`.
- **Migrated service app (richer, self-contained):**
  **`elevatediq-ai/ElevatedIQ-Mono-Repo:apps/aiops-engine/`** (`main`) —
  `src/aiops.py`, `src/intelligence.py`, `main.py`, `test_aiops.py`, `tests/`,
  `Dockerfile`, `requirements.txt`, `Makefile`, `.env.example`. Header
  describes the migrated/expanded variant.
- **Declared home (per stub README):** `elevatediq-ai/eiq-ai:packages/aiops-engine/` → doc-only.
- **Note:** aiops-engine code is **absent from the current `eiq-org`** monorepo
  (not under `apps/`), i.e. it was consolidated into `ElevatedIQ-Mono-Repo`
  and kept in `kushin77/elevatedIQ`, but not carried into the newest repo.
  See §5 owner decision.
- **Pointer for future issues:** `elevatediq-ai/ElevatedIQ-Mono-Repo:apps/aiops-engine/`
  (self-contained service, prefer) with original at
  `kushin77/elevatedIQ:services/aiops-engine/`.

### 3.3 `intelligence` (real but partial — missing `.smart_selector/.llm_auto_triage/.semantic_clustering`)
- **Authoritative refactored copy (self-contained — imports gone):**
  **`elevatediq-ai/eiq-org:shared/automation/scanners/intelligence/`**
  (`main`) — `ensemble_scorer.py`, `main.py`, `Dockerfile`,
  `requirements.txt`, `test_ensemble_scorer.py`. Header states: *"Migrated
  from `kushin77/intelligence/ensemble_scorer.py` and extended for the EIQ DNA
  org flywheel."* The refactor replaced the four original submodels with
  self-contained scorers (keyword/heuristic, semantic-similarity, expertise,
  historical) — **it no longer imports the missing modules**.
- **Incomplete historical copies (same missing imports):**
  `kushin77/GCP-landing-zone:examples/src/intelligence/` (+ nested
  `intelligence/`); `kushin77/GCP-landing-zone:scripts/intelligence/issue-intelligence.py`;
  `kushin77/GCP-landing-zone:docs/intelligence/ENSEMBLE_SCORING_IMPLEMENTATION_GUIDE.md`.
- **Original sibling modules — NOT recoverable:** `filename:smart_selector.py`,
  `filename:llm_auto_triage.py`, `filename:semantic_clustering.py` return **no**
  hits in any accessible repo (`llm_auto_triage` = 0 hits org-wide). Evolved,
  API-different analogs only: `kushin77/uims-spoke:src/smart_selector/` and
  `kushin77/uims-spoke:src/semantic_clustering/`.
- **Pointer for future issues:** recover the package from
  `elevatediq-ai/eiq-org:shared/automation/scanners/intelligence/`; treat the
  eiq-org refactored version as authoritative for the lost modules.

### 3.4 `issue-aggregator` (real but partial — missing `models`/`schema`/`connectors`)
- **Authoritative refactored copy (self-contained):**
  **`elevatediq-ai/eiq-org:shared/automation/ingest/issue-aggregator/`**
  (`main`) — `main.py`, `deduplication.py`, `Dockerfile`, `k8s/`,
  `requirements.txt`, `test_deduplication.py`. Header states: *"Migrated from
  `kushin77/issue-aggregator` and integrated into the EIQ DNA org-product
  flywheel."* Data models are defined inline (pydantic + SQLAlchemy) — the
  standalone `models.py`/`schema.py`/`connectors/` are **not** imported here.
- **Go rewrite (different implementation, not the Python service):**
  `elevatediq-ai/eiq-org:apps/issue-aggregator/` — `*.go` + `go.mod`.
- **Incomplete historical copies (same missing imports):**
  `kushin77/GCP-landing-zone:examples/services/issue-aggregator/` (+ nested
  `issue-aggregator/`).
- **Original sibling modules — NOT recoverable:** searches for `NormalizedIssue`
  and `GitHubConnector` resolve only to `main.py`/`deduplication.py` files;
  no `models.py`/`schema.py`/`connectors/{github,gitlab}.py` exist in any
  accessible repo.
- **Pointer for future issues:** recover from
  `elevatediq-ai/eiq-org:shared/automation/ingest/issue-aggregator/`
  (self-contained Python service).

### 3.5 `llm-triage` — already-good contrast (no recovery needed)
- Complete 4-module package: **`elevatediq-ai/eiq-ai:packages/llm-triage/src/llm_triage/`**
  (`__init__.py`, `classifier.py`, `feedback.py`, `few_shot.py`,
  `integration.py`), mirrored at
  `elevatediq-ai/eiq-org:apps/eiq-ai/packages/llm-triage/src/llm_triage/`.
  This package migrated cleanly (real code + docs) — the reference model of
  what a completed extraction looks like. No action.

## 4. Access decisions (recorded per issue AC #2)

All upstreams are **private but readable** by this org's token → **GRANTED**
(read/cannibalize). None needs `needs-grant`/`fork`/`subtree` to be used as a
reference. If a later phase must **vendor** code verbatim (rather than
harvest), a `subtree`/monorepo-sync of the specific package path is the
fallback — no owner approval required for read, only if writing back upstream.

| Local stub | Upstream home (repo:path) | Access decision |
|---|---|---|
| `ai-agents` | `kushin77/elevatedIQ:services/ai-agents/` (+ `ElevatedIQ-Mono-Repo:apps/agent_framework/` candidate) | **granted** (read) — cannibalize; flag §5.1 |
| `aiops-engine` | `elevatediq-ai/ElevatedIQ-Mono-Repo:apps/aiops-engine/`; original `kushin77/elevatedIQ:services/aiops-engine/` | **granted** (read) — cannibalize; flag §5.2 |
| `intelligence` | `elevatediq-ai/eiq-org:shared/automation/scanners/intelligence/` | **granted** (read) — cannibalize refactored copy; lost modules documented |
| `issue-aggregator` | `elevatediq-ai/eiq-org:shared/automation/ingest/issue-aggregator/` | **granted** (read) — cannibalize refactored copy; lost modules documented |
| `llm-triage` | already complete locally + `elevatediq-ai/eiq-ai:packages/llm-triage/` | **granted** (read) — no action |

## 5. Owner-decision flags and follow-up policy

No access blocker requires a new GitHub issue (nothing is unreachable). Two
**soft** decisions are recorded here for the platform owner rather than filed
as board work (keeps the board stable per the spike's conservative guidance):

1. **Canonical monorepo selection (ai-agents / aiops-engine).** The codebase
   is spread across `kushin77/elevatedIQ` (original, pushed 2026-08-07),
   `elevatediq-ai/ElevatedIQ-Mono-Repo` (migrated apps, pushed 2026-06-16) and
   `elevatediq-ai/eiq-org` (current, pushed 2026-09-01, **but no
   ai-agents/aiops-engine service code**). Owner should state which repo is
   canonical so future phases reference one path.
2. **Lost original modules.** The Phase-2 dissection did not preserve
   `intelligence`'s `smart_selector.py`/`llm_auto_triage.py`/
   `semantic_clustering.py` nor `issue-aggregator`'s
   `models.py`/`schema.py`/`connectors/` in any accessible repo. The eiq-org
   refactor made both packages self-contained, so no reconstruction is needed
   — future issues should use those refactored versions as the source of truth.

## 6. Recon method & evidence log (harvest 2026-09-08)

Read-only GitHub API (all via `gh api`):
- Repo existence/visibility/metadata:
  `repos/elevatediq-ai/eiq-ai`, `repos/elevatediq-ai/ElevatedIQ-Mono-Repo`,
  `repos/elevatediq-ai/eiq-org`, `repos/kushin77/elevatedIQ`,
  `repos/kushin77/GCP-landing-zone`, `repos/kushin77/uims-spoke`;
  `orgs/elevatediq-ai/repos`.
- Targeted trees: `git/trees/<ref>` (top-level and one-level subtree probes
  only) on `eiq-org` (`apps`, `shared/automation/{scanners,ingest}`) and
  `ElevatedIQ-Mono-Repo` (`apps/agent_framework`, `apps/aiops-engine`).
- Path-based listings: `contents/<path>` for
  `eiq-org:shared/automation/scanners/intelligence`,
  `eiq-org:shared/automation/ingest/issue-aggregator`, `eiq-org:apps/*`,
  `kushin77/elevatedIQ:services/ai-agents`, `:services/aiops-engine`,
  `kushin77/GCP-landing-zone:examples/src/intelligence`,
  `:examples/services/issue-aggregator`, `kushin77/uims-spoke:src/*`.
- Raw file headers (via `Accept: application/vnd.github.raw`): eiq-org
  `ensemble_scorer.py`, `deduplication.py`, `main.py`; `eiq-ai`
  `packages/ai-agents/README.md`; `ElevatedIQ-Mono-Repo`
  `apps/agent_framework/main.py`.
- Code search: `search/code?q=<symbol>+org:…` for `smart_selector`,
  `llm_auto_triage`, `semantic_clustering`, `GitHubConnector`,
  `NormalizedIssue`, `agent-framework`; `filename:` searches for
  `smart_selector.py`, `llm_auto_triage.py`, `semantic_clustering.py`.

Local (read-only) sources consulted: stub `.git/config` origin URLs;
`.research/trees/elevatediq.tree.txt` (§`services/ai-agents`, §`services/aiops-engine`,
cross-checked against live `contents/` listings); `.research/trees/repos.txt`;
per-repo `*.tree.txt` maps (confirmed they reflect only the stub content).

API-call record: ~25 targeted `gh api` calls, all read-only; no whole-monorepo
recursive tree was fetched; no repo was cloned; nothing written to `.research`.

## 7. References
- Issue #49 body (spec): `gh api repos/kushin77/agent-orchestrator/issues/49`.
- AGENTS.md / docs/EXECUTION-PLAN.md (lane discipline: one-issue-one-lane,
  recon-only, no product code).
- `docs/CANNIBALIZATION.md` (harvested-asset index; this report adds the
  recovery pointers for the four stub repos listed there).
