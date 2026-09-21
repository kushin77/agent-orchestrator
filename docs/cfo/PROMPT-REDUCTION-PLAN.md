# Prompt-Reduction Program — target 90%+ (inbound + outbound)

CFO office deliverable (issue TBD, parent #1510). Baselines are the
MEASURED figures in `docs/cfo/COST-MODEL-2026-09-20.md` §6. Read that doc
first — every "baseline" cell below cites a row there.

## Headline finding (state up front, not buried)

The repo-tracked instruction/memory set this gate can measure is
**~45,188 bytes (~11,297 tokens, ÷4 estimator) per turn** (COST-MODEL §6.1).
The harness-injected surfaces this session actually carries on top of that
— skill-availability listing (~120 skills with descriptions), MCP-server
instruction blocks, deferred-tool-name list, granted-tool schemas — are, by
direct observation of this very transcript, **larger than the entire
repo-tracked set**, and none of them are files this repo's tooling can
`stat`. **A 90%+ reduction of total inbound bytes is not achievable by
editing repo files alone.** This plan delivers the largest reduction
achievable from levers this repo controls, states the ceiling honestly, and
scopes the mechanical gate (`scripts/check-finops-kpi.sh`) to the part it
can actually measure and prevent from regressing.

## Ranked levers

| # | Lever | Baseline (measured/declared, cited) | Target | Mechanism | Owner SME | Gate that proves it |
|---|---|---|---|---|---|---|
| 1 | **Harness-injected surface reduction (skills, MCP blocks, tool schemas)** | Not repo-measurable; observed this turn to exceed the ~45KB repo baseline (COST-MODEL §6.1) | UNKNOWN — no lever available from this repo; documented as the largest single cost surface and explicitly out of this gate's scope | Harness/session config (fewer enabled plugins/skills per session; narrower tool grants per agent role) — a **harness-owner**, not repo, decision | platform-sme (harness/session config owner) | None in this repo; would need a harness-side token-count API |
| 2 | **Instruction-file dedup: CLAUDE.md vs AGENTS.md vs .cursorrules** | `CLAUDE.md` 1,865 B + `.cursorrules` 1,899 B = 3,764 B injected alongside `AGENTS.md`'s 24,821 B, all three read at session start (COST-MODEL §6.1) | Already near-optimal: `CLAUDE.md` is a 1.8KB **pointer** file ("Read AGENTS.md first... never contradicts it") — it does not restate AGENTS.md content, so measured byte overlap is low, not the ~90% of a true duplicate. `.cursorrules` was not diffed against AGENTS.md this pass — **UNKNOWN (source needed: diff .cursorrules vs AGENTS.md, not run this pass)** | If `.cursorrules` overlaps AGENTS.md content, collapse it to a pointer the same way CLAUDE.md already is | docs-sme | `diff -u .cursorrords AGENTS.md \| wc -l` as a follow-up check (not built this pass — flag, don't fabricate the number) |
| 3 | **Front-loaded goal-first prompts (`/focus` construction rule)** | Qualitative: `~/.claude/CLAUDE.md` already states the rule (goal in first 1-2 sentences) | Not a byte-reduction lever — it changes token **utility**, not token **count**: a subagent that acts correctly from sentence 1 needs fewer clarifying round-trips. Estimated saved turns: UNKNOWN (source needed: round-trip counts per subagent dispatch, not logged in a parseable form this pass) | Already adopted in `~/.claude/CLAUDE.md`; extend to `registry/personas/offices/*/prompts/*.md` (this PR's `cfo-primary@v1.md` follows it) | pmo-sme (dispatch discipline) | Manual: every new `prompts/*.md` reviewed for goal-first ordering |
| 4 | **Prompt caching (Anthropic 1h TTL)** | Declared capability (Anthropic API feature); cache-eligibility per turn in this repo's own gateway code: not instrumented — `gateway/proxy`/`gateway/finops` route/price but do not report cache-hit fraction in any file read this pass | UNKNOWN target — needs a cache-hit metric before a target is meaningful | Ensure the ~45KB stable instruction block (COST-MODEL §6.1) is emitted as a **stable prefix** (it already is, by construction — AGENTS.md/CLAUDE.md/memory load before per-turn content) so the provider can cache it; this is the single highest-leverage **already-true** fact worth stating, not a new build | gateway/platform-sme | None found; would need `telemetry/chat/cache_accounting.py` wired to emit a real hit-rate metric — **not exercised this pass, flagged as a gap** |
| 5 | **Local KB-first retrieval (`governance/knowledge/`, `code-indexing.mcp`)** | `gdc-manifest.yaml` declares `code-indexing.mcp` default `"on"`; `governance/knowledge/` has `indexer.py`, `query.py`, `catalog.json`, `crossref.py`, `live_sync.py` — i.e. the KB-first mechanism **already exists as code** in this repo. `cmr-indexer` MCP (this session's actual indexing tool) **failed to connect** (`CONNECTION_CLOSED`) — reported per policy, not treated as absent | If agents queried `governance/knowledge` / `cmr-indexer` instead of re-reading whole files (as this very CFO pass did — reading `budgets.yaml`, `tiers.yaml`, rate cards directly rather than querying an index), each such lookup could replace a multi-KB file read with a query-sized result. No baseline query-vs-full-read byte comparison was run this pass — **UNKNOWN (source needed: instrument governance/knowledge/query.py call sizes vs the file reads it would replace)** | Prefer `governance/knowledge query` / `cmr-indexer` over raw file reads for repeated lookups (repo-wide agent instruction, not yet in AGENTS.md) | platform-sme + docs-sme (AGENTS.md addition) | Would need a query-vs-read byte-delta test; not built this pass (time-boxed) |
| 6 | **Subagent output compression (cavecrew ~60%)** | Declared by the `caveman:cavecrew` skill family (harness-listed skill description): "~60% smaller" tool-result injection | Adopt cavecrew-investigator/-builder/-reviewer as the default dispatch shape for lookup/mechanical/review work (already policy in `~/.claude/CLAUDE.md` "Subagent-first execution" + this repo's own `docs/EXECUTION-PLAN.md` one-lane doctrine) | pmo-sme | No in-repo gate; the 60% figure is the skill author's own claim, not independently measured here — cited as declared, not measured |
| 7 | **Tier-down defaults (L0 haiku/DeepSeek/Ollama first)** | `gateway/finops/tiers.yaml` L0 = deepseek-v4-flash primary, gemini-2.5-flash fallback; `gateway/proxy/config/routing.yaml` `providerChains.LOW: [deepseek, openai, ollama]` — **already the declared default** | Already at target as declared config; the $ delta vs a Sonnet/Opus-first default is real but not this-repo-measurable without an actual call-volume-by-tier report — **UNKNOWN (source needed: telemetry/metering actual call counts by tier, not populated in this checkout)** | No change needed; verify the declared ladder isn't silently bypassed | gateway/finops owner | `gateway/finops/tests/test_chooser_cheapest.py` (already exists) |
| 8 | **Routing-group fallback to local Ollama** | `routing.yaml` `providerChains` already ends every chain in `ollama` (COST-MODEL §1) — this repo's declared config already has the fallback for LOW and (per file, MED shown truncated) likely MED; HIGH/MAX not confirmed this pass | Confirm HIGH/MAX chains also terminate in `ollama` where capability allows — **not verified this pass** (file read was truncated at MED) | Read remainder of `routing.yaml`, add `ollama` terminal fallback to any chain missing it | gateway/platform-sme | proposal only — no code change made this pass; verification incomplete |
| 9 | **Per-tenant budgets: observe→enforce** | `budget-authority.yaml` frames observe→enforce as a declared rollout axis; `budgets.yaml` has **no `purebliss` tenant entry** — only `tenant-acme/beta/gamma` (illustrative) | Cannot flip a tenant entry that doesn't exist. Proposal: add a `tenant-purebliss` entry with `policy: fallback` (not `stop`, to avoid breaking legitimate spend on a first rollout) once a real monthly budget number is sourced from Finance — **UNKNOWN (source needed: an actual approved purebliss monthly $ cap, not in this repo)** | n/a — proposal only | cfo (finops-sme) | `gateway/finops/tests/test_budget.py` would need a case added for the new tenant — not added this pass |
| 10 | **Metering→budgets feedback loop** | `telemetry/metering/` and `gateway/finops/budget.py` both exist and are separately testable (`telemetry/metering/tests`, `gateway/finops/tests/test_budget.py`) but no single file in this pass confirmed metering output is consumed live by the budget enforcer at runtime vs. only at config-load time | UNKNOWN — needs a runtime trace, not a static read | n/a this pass | gateway/platform-sme | not built |
| 11 | **Handoff/subagent-handback size** | This very CFO-SME dispatch's own handback (below) is a live instance; no repo-wide size cap exists on `SubagentHandback` messages | Adopt a soft convention (state in reports; not enforced) | pmo-sme | none |

## What the mechanical gate actually enforces (scope honesty)

`scripts/check-finops-kpi.sh` sums the byte size of the **repo-tracked**
files in COST-MODEL §6.1 (`AGENTS.md`, `CLAUDE.md`, `.cursorrules`, plus any
file the caller passes) against a ceiling declared in
`registry/personas/offices/cfo/cost-policy.yaml`, and fails if that sum
grows. It intentionally does **not** claim to measure the harness-injected
skill/MCP/tool-schema surface (lever #1) — no file in this repo represents
that surface, so no gate can `stat` it. This scope limit is stated in the
gate's own header comment, not left implicit.

## 90% KPI — measurable definition (for `cost-policy.yaml`)

```
metric: repo_tracked_injected_prompt_bytes
formula: sum(wc -c of AGENTS.md, CLAUDE.md, .cursorrules)
baseline_2026-09-20: 28585   # 24821 + 1865 + 1899, measured this pass
target: no regression above baseline (ratchet), 90% figure is NOT claimed
        against this metric — see "Headline finding" above for why a 90%
        claim against total inbound bytes is not honest from repo-only
        levers
```

## Cadence (for $/day projections)

`fleet/runtimes.yaml` and `docs/EXECUTION-PLAN.md` were the intended cadence
sources; no explicit numeric "wave cap 12" line was located in the portion
of `EXECUTION-PLAN.md` grepped this pass, and no `.fleet/` schedule
directory exists in this worktree (`.fleet/` is untracked per this
session's git status, not part of the checked-in tree read here). **UNKNOWN
(source needed: fleet/runtimes.yaml cadence figures, not parsed this
pass)** — $/day projections in this plan are therefore not computed; only
$/turn-equivalent (rate-card × estimated tokens) is defensible from what
was measured, and even that requires an actual per-turn token count this
pass does not have (COST-MODEL §6.2 — outbound UNKNOWN).

## Top-5 by defensibility (not by unverified $ size)

1. Lever 7 (tier-down defaults) — already implemented, already tested, zero new risk.
2. Lever 8 (Ollama terminal fallback) — cheap to verify/complete, direct $ avoidance on any LOW/MED call that would otherwise hit a paid API.
3. Lever 2 (CLAUDE.md-as-pointer pattern) — already the shape in this repo; worth propagating to `.cursorrules` if the (unrun) diff shows overlap.
4. Lever 6 (cavecrew subagent compression) — already policy, needs no new code, real (if unverified) claimed savings.
5. Lever 5 (KB-first retrieval) — real code exists (`governance/knowledge/`), underused by this very CFO pass (which read raw files), and is the correct target for a follow-up instrumentation issue.
