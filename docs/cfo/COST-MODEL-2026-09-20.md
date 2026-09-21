# Enterprise Cost Model — 2026-09-20

CFO office deliverable (issue TBD, parent #1510). Every figure below is
tagged **MEASURED** (a command was run against this checkout / this
machine and its output is quoted or citable), **DECLARED** (a config file
states a cap/price that is not itself a bill), or **UNKNOWN (source
needed: …)**. `gh api` billing endpoints were not queried — no attempt is
made to fabricate a number from them. Nothing here is a recall-based price;
every price traces to a rate-card file.

## 1. Model API rate cards (DECLARED, point-in-time, not live billing)

Source: `telemetry/metering/rate_cards/*.yaml`.

| Provider | Model | Input $/1M tok | Output $/1M tok | Source |
|---|---|---|---|---|
| Anthropic | claude-haiku-4-5-20251001 | 1.00 | 5.00 | `telemetry/metering/rate_cards/anthropic.yaml` |
| Anthropic | claude-sonnet-5 | 3.00 | 15.00 | same |
| Anthropic | claude-opus-5 | 15.00 | 75.00 | same |
| DeepSeek | deepseek-chat | 0.27 | 1.10 | `telemetry/metering/rate_cards/deepseek.yaml` |
| DeepSeek | deepseek-reasoner | 0.55 | 2.19 | same |
| DeepSeek (finops chooser ladder) | deepseek-v4-flash | 0.21 (blended, illustrative) | — | `gateway/finops/tiers.yaml` L0 |
| DeepSeek (finops chooser ladder) | deepseek-v4-pro | 0.28 (blended, illustrative) | — | `gateway/finops/tiers.yaml` L1 |
| Google | gemini-2.5-flash | 0.30 (blended, illustrative) | — | `gateway/finops/tiers.yaml` L0 fallback |
| OpenAI | any | UNKNOWN (source needed: no `openai.yaml` figures read here — file exists at `telemetry/metering/rate_cards/openai.yaml` but was not opened in this pass; treat as unverified until read) | — | — |
| Ollama (local) | any | 0.00 API cost (compute cost only; see §3) | 0.00 | `telemetry/metering/rate_cards/ollama.yaml` (existence only, not opened) |

`tiers.yaml`'s own header states its `costPerMTok` values are "default
illustrative configuration" — the chooser relies only on relative ordering,
not absolute price. Treat the L0/L1 numbers above as ordering signals, not
billing-grade prices; the `rate_cards/*.yaml` numbers are the billing-grade
ones and should be preferred wherever both exist for the same model.

**Anthropic rate-card provenance note (from the file itself):** the prior PR
attempted to cite the bundled `claude-api` skill's pricing table and was
reverted on review because that table lives in the harness skill cache, not
in this git checkout — there is no in-repo path to cite. This cost model
follows the same rule: skill-cache pricing is never cited as a repo source.

## 2. FinOps budget caps (DECLARED, not spend)

Source: `gateway/finops/budgets.yaml`.

| Tenant | Monthly cap USD | Policy |
|---|---|---|
| tenant-acme | 100.00 | fallback (warn 80%, stop 100%) |
| tenant-beta | 50.00 | stop (warn 80%, stop 100%) |
| tenant-gamma | 25.00 | warn (warn 85%, stop 100%) |
| unprovisioned tenant | — | `defaultPolicy: warn` |

Per-role monthly caps are **consumed from the org chart**, not declared in
`budgets.yaml` (file's own comment, line ~40). CFO role cap: `monthlyBudgetCapUsd: 50`
per `registry/personas/cards/cfo.yaml`.

Enforcement mode: `gateway/finops/budget-authority.yaml` line 87 declares the
axis `each_member_of_inline: ["observe", "enforce"]` — i.e. every budget
enforcement class in this repo is currently somewhere on an **observe →
enforce ladder**, described as "the safe-rollout control." No single file
states "purebliss tenant is in observe mode" in one line; the concrete mode
per tenant is runtime-provisioned by the control plane per `budgets.yaml`'s
own header ("runtime per-tenant budget set is provisioned by the control
plane"), not fixed in this git checkout. See §7 for why this blocks an
unconditional observe→enforce flip.

## 3. On-prem cluster (DECLARED inventory, no measured $ found in-repo)

Source: user memory `project-fleet-cutover-policy.md` (HA container pair on
shared-services; nodes referenced elsewhere as `.31`/`.42`/`.56`) and the
CFO-SME task brief. This repo (`agent-orchestrator`) does not itself declare
node specs, power, or hosting cost — those live in `shared-services`
(`infra/envs/onprem`), which was **not modified** per constraints and was
only read-only surveyed at the level available without a dedicated subagent
pass in this run (time-boxed). On-prem $/month: **UNKNOWN (source needed:
shared-services infra/envs/onprem cost declaration or hosting invoice)**.

## 4. GCP / Cloud Build / Cloud Run (DECLARED config only, no $ found)

This repo's own Cloud Build config: `cloudbuild*.yaml` at repo root and
under `control-plane/`/`portal/` were not exhaustively enumerated in this
pass; `CLOUD_LOGGING_ONLY` was set on verify/web-image builds per recent
commit `098d304c`, which reduces Cloud Logging cost but is not itself a
dollar figure. GCP project id(s), Cloud Run service counts, Artifact
Registry storage: **UNKNOWN (source needed: `gcloud` billing export or
project IaC in this repo / shared-services, not queried this pass — `gh
api`-equivalent billing calls are out of scope per task brief)**.

capital-underwriting Cloud Build triggers: per user memory
(`project-capital-retired.md`), retiring 2026-09-18 — i.e. as of this
model's date (2026-09-20) they are past their stated retirement date; this
repo cannot confirm current billing state without a read-only pass in that
repo (not performed this run; report this as **a scoping gap**, not a
number).

## 5. Cloudflare, Anthropic org spend, other SaaS

**UNKNOWN (source needed: no Cloudflare zone config, no Anthropic
organization billing export, found under any surveyed path in this repo)**.

## 6. Token/prompt cost model — inbound and outbound, MEASURED sizes

All sizes below are `wc -c` byte counts against the actual files in this
worktree / this machine, run 2026-09-20. Bytes are not tokens (roughly
÷4 for English prose/markdown, ÷3–3.5 for YAML/code — used as a rough
estimator only, flagged as such).

### 6.1 Per-turn instruction/memory injection (inbound, repo-tracked + user-global)

| File | Bytes (measured) | ~tokens (÷4, estimator) | Source |
|---|---|---|---|
| `AGENTS.md` (this repo) | 24,821 | ~6,205 | `wc -c AGENTS.md` |
| `CLAUDE.md` (this repo, pointer file) | 1,865 | ~466 | `wc -c CLAUDE.md` |
| `.cursorrules` (this repo) | 1,899 | ~475 | `wc -c .cursorrules` |
| `~/.claude/CLAUDE.md` (user global) | 4,248 | ~1,062 | `wc -c ~/.claude/CLAUDE.md` |
| `~/.claude/rules/context7.md` (user global) | 1,630 | ~408 | `wc -c ~/.claude/rules/context7.md` |
| `MEMORY.md` (repo-scoped auto-memory index) | 2,036 | ~509 | `wc -c ~/.claude/projects/-home-akushnir-agent-orchestrator/memory/MEMORY.md` |
| 4 linked memory notes sampled (fleet-cutover, purebliss-prod-state, assign-issues, console-live) | 8,689 | ~2,172 | same dir, `wc -c` on the 4 files (full memory dir has ~15 note files per MEMORY.md bullet list; not all sampled) |
| **Repo-tracked + user-global subtotal (this sample)** | **~45,188 bytes** | **~11,297 tokens** | sum of above |

This subtotal is **the gate's actual scope** (§8) because it is the only
part of the injected prompt this repo's tooling can `stat` on disk. It is
**not** the full inbound prompt:

- The harness additionally injects, on every turn of this session: a
  skill-availability listing (in this session, ~120 skills with
  descriptions — this single system-reminder block is visibly larger than
  `AGENTS.md`, but its byte size is harness-rendered per-session and not a
  file this repo's gate can read), the deferred-tool-name list, the
  MCP-server instruction blocks (context7, claude-in-chrome, Claude Docs —
  three blocks seen this turn alone), and tool schemas for every tool
  granted to the session. None of these are measured here because none of
  them exist as a stat-able file in this git checkout — **UNKNOWN (source
  needed: harness-side prompt-assembly log or token-count API response,
  not available to a file-reading agent)**.
- Tool-call results (file reads, `git` output, subagent handbacks) are
  inbound context on later turns and are workload-dependent, not a fixed
  per-turn cost — no single number applies; see §6.3 for the proxy used.

### 6.2 Outbound (agent output, subagent handbacks)

No in-repo measurement of actual assistant-output token counts was found
(no local token-accounting log surfaced in `telemetry/chat/` beyond code —
`telemetry/chat/cache_accounting.py` exists as logic, not as a populated
data file in this checkout). **UNKNOWN (source needed: telemetry/chat
readmodel output for a real tenant, or `~/.claude/projects/*/*.jsonl`
per-message token fields, not parsed in this pass)**.

Declared mechanism for compression: the `caveman:cavecrew` skill family
states subagent output is "caveman-compressed so the tool-result injected
back into main context is ~60% smaller" (skill description, harness-listed,
not a repo file — cited as declared, not measured).

### 6.3 Session-log proxy for prompt/turn volume (MEASURED, proxy only)

`du -sh ~/.claude/projects/*/` (this machine, 2026-09-20):

| Project dir | Size |
|---|---|
| `-home-akushnir-cmr` | 432M |
| `-home-akushnir-agent-orchestrator` | 306M |
| `-home-akushnir-ERP-CRM` | 137M |
| `-home-akushnir-ao-worktrees-ao-234-487c3aa7` | 63M |
| `-home-akushnir-diagrams` | 53M |
| `-home-akushnir-asterisk` | 40M |
| `-home-akushnir-code-indexing` | 39M |
| `-home-akushnir-shared-services` | 28M |
| `-home-akushnir-googleworkspace` | 21M |
| `-home-akushnir-twilio-twilio` | 9.9M |

This is a **proxy** for cumulative session I/O (JSONL transcripts include
both prompt and completion text plus tool payloads), not a token count and
not a $ figure — flagged UNKNOWN for $ conversion pending a real
token-field parse of the JSONL.

## 7. What blocks an immediate observe→enforce flip (relevant to plan §PROMPT-REDUCTION and IaC §3)

`gateway/finops/budget-authority.yaml` frames observe→enforce as a
"safe-rollout control," and `budgets.yaml`'s per-tenant mode is
control-plane-provisioned at runtime, not a static file value in this repo.
There is no `tenant: purebliss` entry in the sampled `budgets.yaml` (only
`tenant-acme/beta/gamma` — illustrative/test tenants). Flipping a
nonexistent tenant entry to `enforce` is not a real config change; see
`docs/cfo/PROMPT-REDUCTION-PLAN.md` §Config flips for what was and was not
safely flippable this pass.

## 8. Sources index

- `telemetry/metering/rate_cards/anthropic.yaml`, `deepseek.yaml`
- `gateway/finops/budgets.yaml`, `tiers.yaml`, `budget-authority.yaml`, `README.md`
- `registry/personas/cards/cfo.yaml`
- `gateway/proxy/config/routing.yaml`
- `governance/knowledge/` (catalog.json, indexer.py, query.py — KB surface, see reduction plan)
- `gdc-manifest.yaml` (`code-indexing.mcp`, default "on")
- `~/.claude/CLAUDE.md`, `~/.claude/rules/context7.md`, `~/.claude/projects/-home-akushnir-agent-orchestrator/memory/*`
- `wc -c` and `du -sh` commands run 2026-09-20, this checkout / this machine
- `cmr-indexer` MCP: **connection failure** this session (`CONNECTION_CLOSED`) — reported, not treated as absent; its actual index size/hit-rate is therefore UNKNOWN this pass.
