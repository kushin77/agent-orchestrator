# ARCHITECTURE-2026-09 — the control chain as it actually runs (2026-09-20)

Issue: #1650 (Parent: #1510). Supersedes no ADR; corrects the naming this repo's
own docs had gotten wrong. Every claim below cites a path or a command output —
no aspirational prose (see `CONSTRAINTS` in the issue).

**Relationship to `docs/ARCHITECTURE.md`:** that document remains the
five-pillar design reference (Agent Registry & Profiling / Model Gateways /
State-machine execution / Security & guardrails / Observability) and its
`docs/README.md` index row stays "source of truth" for **pillar design**. This
document is the **as-built control chain** — what actually dispatches a unit of
work, top to bottom, today — and is the source of truth for **that** question.
Where the two disagree, this document is newer and wins until `ARCHITECTURE.md`
is refreshed to match (tracked below as a MISSING item).

## The correction this document exists to make

Two of this repo's own READMEs currently say a persona is "the orchestration
half" of the fleet. That claim is **superseded**. Per
[ADR-0033](decision-records/ADR-0033-paperclip-hermes-naming-resolution.md)
(accepted 2026-09-20, issue #1514, parent #1510):

> **There is exactly one top-level orchestrator of agent work: `fleet/brain.py`
> + `governance/dispatch/`.** Neither "Paperclip" nor "Hermes" is the top-level
> orchestrator; no artifact of either name is authoritative for agent control.

`integrations/paperclip/reporting/README.md` line 3 still reads "the
orchestration half is hermes ([ADR-0012])" — this is the exact stale claim
ADR-0033 corrects. **DECLARED-ONLY / STALE:** flagged here, not fixed in this
PR's file set (reporting/ is outside this lane's declared scope — see
Constraints); tracked as issue #1651 (filed below, assignee kushin77, Parent:
#1510).

## Layer diagram (top to bottom)

```
 Operator / Owner (GitHub issue, PR, or paperclip ticket)
        │
        ▼
 ┌───────────────────────────────────────────────────────────┐
 │ LAYER 0 — Paperclip (reporting/planning half)              │
 │ integrations/paperclip/**                                  │
 │ role: ticket/heartbeat/budget PROJECTION, module-brief      │
 │ composition, HTTP surface projection. NOT a runtime.        │
 └───────────────────────────────────────────────────────────┘
        │  ticket = the single join node (ADR-0014)
        ▼
 ┌───────────────────────────────────────────────────────────┐
 │ LAYER 1 — PMO                                               │
 │ governance/pmo/**                                            │
 │ role: read-only DERIVED VIEWS over the ticket graph          │
 │ (deps/lanes/report/raid/aging/gates). No ledger, no cache.   │
 └───────────────────────────────────────────────────────────┘
        │  informs dispatch decisions (not itself a dispatcher)
        ▼
 ┌───────────────────────────────────────────────────────────┐
 │ LAYER 2 — TOP-LEVEL ORCHESTRATOR (the actual control point) │
 │ fleet/brain.py + governance/dispatch/                        │
 │ role: THE sole authority for agent control (ADR-0033).       │
 └───────────────────────────────────────────────────────────┘
        │  routes a worker persona onto a provider via routingGroups
        ▼
 ┌───────────────────────────────────────────────────────────┐
 │ LAYER 3 — Hermes (routing/executor persona) + agent roster   │
 │ registry/profiles/seeds/hermes.1.0.0.yaml, gateway/providers/│
 │ role: ONE of five worker personas + ONE inference adapter.   │
 │ Not an orchestrator (ADR-0033 H1/H2/H3).                      │
 └───────────────────────────────────────────────────────────┘
        │  gateway/proxy/config/routing.yaml routingGroups.purebliss-team
        ▼
 ┌───────────────────────────────────────────────────────────┐
 │ LAYER 4 — Provider gateways: Claude / DeepSeek / Ollama /     │
 │ Paperclip (P2 adapter) — each a provider, fallback = ollama   │
 │ gateway/catalog/modules/*, gateway/providers/*.py             │
 └───────────────────────────────────────────────────────────┘
        │  work lands as a commit / PR
        ▼
 ┌───────────────────────────────────────────────────────────┐
 │ LAYER 5 — lanes / gates / attestation                        │
 │ scripts/verify.sh (make verify), fleet/runner/ (merge train),│
 │ make land, master-attestation                                 │
 └───────────────────────────────────────────────────────────┘
```

---

## Layer 0 — Paperclip (reporting/planning half)

**Purpose.** The fleet's reporting and planning-surface agent: composes
per-module briefs, projects fleet truth over an upstream-shaped HTTP surface,
and maps board state to tickets/heartbeats/budget. It is **not** an
orchestrator — ADR-0013 forbids the upstream `paperclip.ing` product from
acting as "a second authoritative runtime," and ADR-0033 makes the correction
explicit for every Paperclip artifact (P1 persona, P2 adapter, P3 vendored
module, P4 upstream product — none authoritative for agent control).

**IMPLEMENTED** (`integrations/paperclip/`, the one canonical adapter module,
ADR-0016 / `scripts/check-paperclip-canonical-module.sh`):
- `client.py` — `Transport` protocol, `HttpTransport`/`FixtureTransport`, company scoping, `Authorization: Bearer`, `X-Paperclip-Run-Id`
- `mapping.py` — deterministic mapper: seeds/persona cards → agents, claims + board snapshot → tickets, budget rail → costs, rung beats → heartbeats
- `model.py`, `cli.py` — `plan` (offline dry-run), `check` (tri-state conformance), `push` (network path)
- `budget.py` — budget rail (scope, currency, hard-stop, ticket receipt)
- `adapters/{approvals,heartbeat,secrets,skills}/` — per-family parity projections (EPIC #410)
- `reporting/{capability,composer,model,policy,audit,cli}.py` — module-brief composition, claim-resolution policy (`claim-policy.json`), frozen schema (`brief_schema.json`), append-only audit at `.verify/module-brief-audit.jsonl`
- `api/{contracts,surface,taxonomy,company,health,openapi,errors}.py` — HTTP surface projection (`GET /api/health`, `/api/openapi.json`, `/api/companies/{companyId}/...`); real transport is `portal/server/bridge.py` (SPoG #339), this package owns only the projection
- `gateway/providers/paperclip.py` — the P2 OpenAI-compatible inference adapter routing `paperclip-planner`

**DECLARED-ONLY:** none found in this pass beyond the STALE-doc item above.

**MISSING:** `integrations/paperclip/reporting/README.md` line 3's "orchestration
half is hermes" claim needs a correction citing ADR-0033. **Filed:** issue #1652
(assignee kushin77, Parent: #1510) — "docs: correct stale 'hermes is the
orchestration half' claim in integrations/paperclip/reporting/README.md against
ADR-0033."

---

## Layer 1 — PMO (`governance/pmo/`)

**Purpose.** Derived, read-only views over the ticket graph built by
`governance/ticket` (ADR-0014: the paperclip ticket is the single join node —
a projection, never authority). Deliberately **not a store**: no ledger, no
cache that survives a rebuild, no second source of `status`.

**IMPLEMENTED:**
- `governance/pmo/graph.py` — builds the ticket graph in memory from committed ledgers
- `governance/pmo/views.py`, `cli.py` — subcommands `deps` / `lanes` / `report` / `raid` / `aging` / `gates` (#635)
- Gate: `scripts/check-pmo-rollup.sh` (in `make verify`), `make pmo` target
- Exit contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS (never reports a pass on an unbuildable graph)

**DECLARED-ONLY:** the CMR doctrine this package implements is
`vendor/CMR/docs/PROGRAM-MANAGEMENT.md` (pinned submodule, unpopulated in a
fresh worktree per `governance/pmo/README.md` — quoted, not linked). Measured
2026-09-14: `git ls-tree -r --name-only origin/master | grep -i pmo` was
**empty** before issue #403 — `governance/pmo/` is the first producer of this
layer in this repo.

**MISSING:** none observed; the layer is intentionally thin.

**Note on the brief's PR citations:** PRs #1578/#1575 were named in the task
brief as PMO-relevant but were not independently verified in this pass (`gh pr
view` not run against them); this document does not cite them as evidence for
that reason — a gap, not a claim.

---

## Layer 2 — the actual top-level orchestrator

**Purpose.** `fleet/brain.py` + `governance/dispatch/` is the sole authority
for agent control (ADR-0033, correcting ADR-0012/ADR-0013's implicit framing
that had been misread org-wide as "Paperclip/Hermes is the orchestrator").

**IMPLEMENTED:** `fleet/brain.py`, `governance/dispatch/` (dispatch CLI, audit,
tier policy — `governance/dispatch/cli.py`, `governance/dispatch/audit.py`,
`governance/dispatch/tier-policy.json`, `governance/dispatch/tiered.py` per
this branch's own working tree — this repo's dispatch lane, `issue-708`, is
this very worktree's parent branch). The exposed control surface for this
layer is the 16-verb `board.*` namespace in
`control-plane/control/verbs.yaml` — `board.status`, `board.held`,
`board.eligible`, `board.audit`, `board.liveness`, `board.dangling`,
`board.focus`, `board.pool`, `board.claim`, `board.dispatch`, `board.release`,
`board.reap`, `board.snapshot`, `board.trigger`, `board.queue`,
`board.freshness` — all backed by `governance/dispatch/cli.py`, gated by
`governance/dispatch/tests/` (see `docs/ABILITIES.md` for the full verb
table).

**DECLARED-ONLY / MISSING:** a from-scratch narrative diagram of
`fleet/brain.py`'s internal dispatch loop is out of this PR's scope (would
require reading the full module, which the reporting/PMO/Hermes fork pass
above did not cover); tracked as a follow-up, not fabricated here. **Filed:**
issue #1653 (assignee kushin77, Parent: #1510) — "docs: diagram fleet/brain.py
+ governance/dispatch/ internal dispatch loop for ARCHITECTURE-2026-09.md."

---

## Layer 3 — Hermes (routing/executor persona) + the five-agent roster

**Purpose.** Per ADR-0033's H1/H2/H3 table: Hermes is **not** an orchestrator
in this repo. H1 = vendored capability-registry/model-tiering contract
(mapped, never coupled). H2 = keyless local inference adapter. H3 = upstream
`kushin77/hermes-agents` product — deployable, not running, not wired in
(ADR-0012 Context §7).

**IMPLEMENTED:**
- `registry/profiles/seeds/hermes.1.0.0.yaml` — AgentProfile seed, "hermes (coding / capability-routing worker) v1.0.0"; `defaultModelTier: MED`; `toolAllowlist` includes `gh_issue`, `gh_pr`, `git_worktree`, `shell_exec`; provenance cites `kushin77/hermes-agents` `capability_registry.py` + `model_tiering.py`
- `registry/personas/cards/hermes.yaml` — persona card
- `gateway/catalog/modules/hermes/module.json` — declares hermes as `model-gateway`/`provider`/`local`; `source.repo: kushin77/hermes-agents` (plural — confirmed correct in-file per ADR-0033: `kushin77/hermes-agent` singular returns HTTP 404, is only the docker-compose service name); package `gateway.providers.hermes`; feature `ollama-compatible-chat` (keyless)
- `gateway/providers/hermes.py` — the H2 adapter implementation

**Confirmed:** agent-orchestrator's entire use of "Hermes" is exactly one
inference adapter (H2) plus one worker persona/seed — nothing more. No
orchestration capability exists anywhere in this repo's hermes surfaces.

**The five-agent roster** (`registry/packs/releases/purebliss-team.1.0.0.yaml`,
EPIC #253): `ollama`, `paperclip`, `hermes`, `deepseek`, `claude` — each an
AgentProfile + PersonaCard bundled into one installable pack. Only the
`claude` seed's `capabilitySet` declares `orchestrate` (a worker-level
capability tag, distinct from the Layer-2 top-level orchestrator — noted here
so the distinction is not blurred a second time).

**DECLARED-ONLY:** H3 (`kushin77/hermes-agents` upstream Flask product, port
9501, per ADR-0012 Context §7) — not wired into this repo at all.

---

## Layer 4 — provider gateways: Claude / DeepSeek / Ollama / Paperclip

**Purpose.** Transport for each of the five agents to its model provider, with
a uniform local-Ollama fallback (cloud → local graceful degradation).

**IMPLEMENTED:**
- `gateway/proxy/config/routing.yaml` `routingGroups.purebliss-team` — 5 agents, each `{ provider: <p>, fallbacks: [ollama] }`: `ollama: {provider: ollama}`, `paperclip: {provider: paperclip, fallbacks: [ollama]}`, `hermes: {provider: hermes, fallbacks: [ollama]}`, `deepseek: {provider: deepseek, fallbacks: [ollama]}`, `claude: {provider: anthropic, fallbacks: [ollama]}`; `retryMaxAttempts: 2`
- `gateway/catalog/modules/{claude-anthropic,ollama,copilot,hermes,nous,openai,gemini,deepseek,paperclip}/` — one catalog module per provider surface (9 directories; the roster above wires 5 of them into `purebliss-team`, the remaining 4 are catalog-declared but outside this routing group)
- `gateway/providers/{anthropic,copilot,deepseek,gemini,hermes,nous,ollama,openai,paperclip}.py` — the adapter implementations

**Tiers:** the task brief cites `docs/MODEL-PROFILES.md`; that file **does not
exist in this repo** — `find . -iname MODEL-PROFILES.md` (excluding `vendor/`)
returns nothing. The canonical copy is `kushin77/CMR docs/MODEL-PROFILES.md`
(per the `claude` seed's own provenance comment in
`registry/packs/releases/purebliss-team.1.0.0.yaml`), i.e. it is a **hub**
doctrine document, not a local one. **MISSING (doc-only):** this repo has no
local FinOps-tier reference doc of its own; tiers are declared per-seed
(`defaultModelTier: LOW|MED|...`) rather than centrally. Not filing a new issue
for this — it is consistent with the CMR hub/spoke split documented in
`docs/CTO-OFFICE.md` below.

---

## Layer 5 — lanes / gates / attestation

**Purpose.** The mechanical path from a green branch to a landed, attested
`master`.

**IMPLEMENTED:**
- `scripts/verify.sh` — the honest composite gate (GR-12), `make verify` target (Makefile:153)
- `fleet/runner/` — the merge train: `plan.py`, `verify.py`, `merge.py`, `train.py`, `evidence.py`, `capacity.py`, `cli.py`
- `make land ISSUE=<n>` (Makefile:851) — dry-run by default (prints the plan, changes nothing); `AO_LAND_APPLY=1 make land ISSUE=<n>` for a real landing; refused unless pre-merge attestation is green and names the commit (`governance/merge`, `scripts/check-landing.sh`)
- `make master-attestation` (Makefile:869) — `governance/landing/cli.py write-master-attestation`; **not yet wired into `scripts/verify.sh`** itself per the Makefile's own comment (held by open PR #1127 at the time the target was added) — a **DECLARED-ONLY** seam, by the Makefile's own admission
- Gate-lock / merge-train docs: `docs/PR-RUNNER.md`, `docs/PR-QUEUE.md`, `docs/EXECUTION-PLAN.md`

**MISSING:** none beyond the already-tracked `master-attestation` ↔
`scripts/verify.sh` wiring gap (owned by #1127, not re-filed here).

---

## Evidence commands run for this document

- `git show --stat d0eecdc0` — confirmed ADR-0033's landing commit and scope
- `find . -iname MODEL-PROFILES.md` (excl. `vendor/`) — empty, confirms hub-only doc
- `grep -n -i "hermes\|paperclip\|purebliss-team" gateway/proxy/config/routing.yaml` — confirmed the 5-agent routing group and fallback wiring
- `git show origin/issue-purebliss-single-tenant-org:identity/rbac/presets/cto-superadmin.yaml` and `:registry/personas/offices/cto/charter.yaml` — confirmed PR #1580's not-yet-merged CTO-office RBAC/charter content (see `docs/CTO-OFFICE.md`)
