# gateway/finops — FinOps model chooser

Owner lane: **gateway** (issue `kushin77/agent-orchestrator#17`, "13 FinOps model
chooser"). Pillar 2 · Model Gateways, phase 2. See
[`docs/EXECUTION-PLAN.md`](../../docs/EXECUTION-PLAN.md) for the lane contract
(one issue = one lane; this lane owns `gateway/finops/**` only).

## Purpose

The FinOps model chooser is the **cheapest-capable-model-wins routing ladder**
(L0/L1/L2) that governs commercial-model spend per tenant. Before any model
call, the gateway proxy (issue #16, later wave) and the state-machine engine
ask the chooser which tier and which concrete model to use. The chooser:

- defaults every task to the **cheapest model that can plausibly finish it**,
- escalates only **on observed difficulty or failure** — never pre-emptively,
- enforces the **security guardrail**: security/IaC/governance work never
  routes below L1-equivalent,
- enforces **per-tenant budgets** (stop / warn / fallback policies),
- picks a **health-aware fallback model** when the primary is unhealthy, and
- emits **cost attribution** records to the Phase-5 metering store.

This directory implements and owns the chooser **contract**; the gateway proxy
consumes it through `chooser.py`.

## Vocabulary (consumed, not redefined)

The chooser consumes ids already defined by earlier lanes and never redefines
them:

| Vocabulary | Source | How the chooser uses it |
|---|---|---|
| `modelTierHint` `low\|med\|high` | [`registry/prompts`](../../registry/prompts/README.md) prompt-module contract | each ladder tier carries the hint it maps to (`low` → L0, `med` → L1, `high` → L2) |
| registry profile tiers `LOW/MED/HIGH` | [`registry/profiles/catalog.yaml`](../../registry/profiles/catalog.yaml) | each ladder tier carries the `registryTier` it maps to |
| capability ids (`code-author`, `security-review`, …) | [`registry/profiles/catalog.yaml`](../../registry/profiles/catalog.yaml) | each task class references a catalog capability id |
| L0/L1/L2 ladder + security floor | fleet `MODEL-PROFILES` (cannibalized) | the routing ladder itself |

The `MAX` registry tier (advisor-only, decisions-only) is **out of scope**: the
chooser never returns it as a routing target.

## Files

| Path | Purpose |
|---|---|
| [`tiers.yaml`](tiers.yaml) | Declarative model-tier table: ladder, task classes, security floor, escalation thresholds. |
| [`budgets.yaml`](budgets.yaml) | Seed per-tenant budget config (policy vocabulary `stop\|warn\|fallback`) + the per-role policy defaults (the role **caps** are consumed from `registry/personas/`). |
| [`loader.py`](loader.py) | Tier-table loader + data model; fail-closed validation; catalog-capability parity helper. |
| [`complexity.py`](complexity.py) | Difficulty scorer (0-100) driving escalation (hermes-adapted). |
| [`budget.py`](budget.py) | Per-tenant **and** per-role budget enforcement + `budgets.yaml` loader + the org-chart cap consumer. |
| [`metering.py`](metering.py) | `MeteringSink` hook + `CallRecord` / `RefusalRecord` + JSONL/list sinks (Phase-5 interface). |
| [`chooser.py`](chooser.py) | `ModelChooser` — the router the gateway calls. |
| [`cli.py`](cli.py) | Offline CLI (`table` / `budgets` / `choose` / `demo`) for evidence and demos. |
| [`tests/`](tests/) | pytest suite (cheapest-capable, escalation, security-never-L0, budgets, **role caps**, metering, health). |

## The tier table

`tiers.yaml` declares the ladder (ordered cheapest-first by cost), the models
that can serve each tier, the per-task-class routing policy, the security
floor, and the difficulty thresholds.

```yaml
ladder:
  L0:   # mechanical — cheapest capable (modelTierHint low,  registryTier LOW)
  L1:   # implementation — security floor (modelTierHint med, registryTier MED)
  L2:   # architecture — escalated top tier (modelTierHint high, registryTier HIGH)
security:
  floorTier: L1   # guarded classes never route below this
escalation:
  thresholds: { L0: 40.0, L1: 70.0 }   # difficulty score -> raise a tier
```

Each task class declares its **cheapest-capable default tier**, its
**per-task-type escalation cap** (`maxTier`), and an optional **guardrail**
(`security`, `iac`, or `governance`):

```yaml
taskClasses:
  code-author:       { capability: code-author, defaultTier: L0, maxTier: L1 }
  research:          { capability: research,    defaultTier: L0, maxTier: L2 }
  security-review:   { capability: security-review, defaultTier: L1, maxTier: L2, guardrail: security }
```

## How the chooser works

`ModelChooser.choose(task_class, tenant_id, agent_id, complexity, …)` runs the
selection pipeline below and returns a `Choice` (tier + concrete model +
estimated USD cost + reasons + budget action).

1. **Cheapest capable default** — the task class's `defaultTier`.
2. **Security guardrail** — a task class tagged `security`/`iac`/`governance`
   is floored at `security.floorTier` (L1) and can **never** resolve to L0,
   even if a caller supplies a trivial difficulty score or the config
   mistakenly declares a low default.
3. **Difficulty escalation** — the difficulty scorer (0-100) is compared with
   `escalation.thresholds`; each threshold cleared raises the target one tier,
   capped by the class's `maxTier`. A task can also escalate **on failure**
   via `escalate_on_failure(choice)` (clamped to `maxTier`;
   `EscalationCapReached` past the cap).
4. **Budget pre-flight** — per **role** first, then per **tenant**. The
   `RoleBudgetEnforcer` resolves the cap for the persona the call is dispatched
   for (`agent_id`) and classifies the call: `stop` raises `RoleBudgetBlocked`
   **and meters a `RefusalRecord`**; `warn` flags it; `fallback` downgrades the
   tier. The tenant `BudgetDecisionMaker` then classifies the same call: `stop`
   raises `BudgetBlocked`; `fallback` downgrades the tier toward the class's
   cheapest-capable floor (never below it, never below the security floor)
   instead of blocking; `warn` flags the call. Spend commits to the ledger only
   after the call is actually made.
5. **Health-aware model pick** — within the chosen tier the cheapest **healthy**
   model wins. Health is an injected signal (dict or predicate of model id →
   healthy). An unhealthy primary falls back to the next candidate in the same
   tier; an entirely unhealthy tier escalates one tier (respecting the cap) or
   raises `NoHealthyModelError`.
6. **Cost attribution** — every non-blocked choice emits one `CallRecord`
   (tenant, agent, task class, tier, model, provider, estimated cost, budget
   action) to the injected `MeteringSink`.

### Per-tenant budgets

`budgets.yaml` seeds per-tenant lines: monthly budget in USD, policy, warn and
hard-cap percentages. `BudgetDecisionMaker.check()` is a pure decision over an
injected `BudgetLedger`, so the gateway wires real billing state without
changing the chooser:

| Policy | Below `warnAtPct` | From `warnAtPct` on | At/above `hardCapPct` |
|---|---|---|---|
| `stop` | allow | **stop** (blocked) | stop (blocked) |
| `warn` | allow | allow + warning flag | stop (blocked) |
| `fallback` | allow | downgrade tier toward class floor, else flag | stop (blocked) |

### Per-role monthly caps (workbook-2)

The workbook declares a **monthly cap per C-suite role** (CEO 300 / CTO 250 /
COO 100 / CFO 50 / CMO 200 USD). The caps are **consumed, never redefined**:
they live in the registry's workbook-1 declaration and this lane only reads
them.

| Where | What it declares |
|---|---|
| [`registry/personas/org-chart.yaml`](../../registry/personas/org-chart.yaml) | `roles[].monthlyBudgetCapUsd` — the cap, plus `defaultModelTier` and `heartbeatSchedule` |
| [`registry/personas/cards/<role>.yaml`](../../registry/personas/cards/) | the bound persona card, whose cap **must agree** with the chart |
| [`budget.py`](budget.py) `load_role_budgets()` | reads both, asserts they agree, and fails closed on drift / a missing card / a non-numeric cap |
| [`budgets.yaml`](budgets.yaml) `roles:` | documents the **policy** and points at the cap source — it never restates a number |

The chooser checks the role cap for the persona a call is dispatched for
(``agent_id``) **before** the tenant budget, because the narrower ceiling is the
one that must not be crossed: a role at its cap refuses spend even when the
tenant overall is well inside its own budget. A role with no declared cap has no
role ceiling and falls through to the tenant axis — it is not silently
unbudgeted, merely governed by the tenant.

| Policy | Below the cap | At/above the cap |
|---|---|---|
| `stop` (role default) | allow | **refuse spend** (`RoleBudgetBlocked`) |
| `warn` | allow | allow + warning flag |
| `fallback` | allow | downgrade the tier toward the class floor, else flag |

A role cap **refusal is metered**: ``RoleBudgetBlocked`` carries the
``BudgetDecision``, and the chooser emits one ``RefusalRecord`` (scope `role`,
the cap, and the percentage used) to the metering sink before raising — a budget
alert is raised, never silently absorbed.

The cap path is **deterministic arithmetic only** — a percentage comparison over
the spend ledger, with no model call and no token spend. This is the workbook
mechanical rule *"zero-token arithmetic"* (CFO row): deciding whether tokens may
be spent must never itself spend tokens, and must not depend on a generative
step. The refusal is **mutation-proved**: `tests/test_role_budget.py` disables
the hard-cap comparison and then the over-cap policy mapping in
``RoleBudget.decide`` and asserts the refusal probes die under each mutant.

### Metering hook (Phase 5)

`metering.py` defines the `MeteringSink` protocol the gateway calls with a
`CallRecord`. The telemetry pillar (phase 5, issue #31) consumes these records
for per-tenant/per-agent/per-task-class cost attribution. Shipped sinks:
`JsonlMeteringSink` (append-only JSONL, fleet model-call-audit shape),
`ListMeteringSink` (tests), `NoopMeteringSink` (default).

A ``RefusalRecord`` is the refusal counterpart: it carries the scope that
refused (`role` or `tenant`), the cap, and the percentage used, so a cap
decision is attributed even though no model call happened. The per-role axis
meters its refusals; the tenant axis deliberately still emits nothing on a
``STOP``, preserving the issue-#17 contract.

## Usage

All commands run from the repo root; the modules are self-contained Python 3
(stdlib + PyYAML; no network, no third-party installs).

```bash
# Inspect the tier table and seeded budgets
python3 gateway/finops/cli.py table
python3 gateway/finops/cli.py budgets

# Route one task (JSON result)
python3 gateway/finops/cli.py choose --task-class research --complexity 25 --tenant tenant-acme
python3 gateway/finops/cli.py choose --task-class security-review --complexity 10 --tenant tenant-gamma
python3 gateway/finops/cli.py choose --task-class research --complexity 85 --tenant tenant-acme

# Scripted end-to-end demo (budgets + health + JSONL metering)
python3 gateway/finops/cli.py demo --meter /tmp/ao17-meter.jsonl

# Tests
python3 -m pytest gateway/finops/tests -q
```

## Acceptance criteria (issue #17)

| Criterion | Where |
|---|---|
| Model tier table (cheapest-capable) per task class + difficulty escalation | `tiers.yaml`, `loader.py`, `complexity.py`, `chooser.py` |
| Security/IaC/governance never below L1 (guardrail) | `chooser.py` floor clamp + negative tests |
| Per-tenant budget enforcement at the gateway (stop/warn/fallback) | `budget.py`, hook contract in `chooser._check_budget` |
| Cost attribution per agent/tenant/model to the metering store | `metering.py` `MeteringSink` + `CallRecord` |
| Testable router (task + budgets + health) | `tests/` (injectable enforcer + health signal) |

## Acceptance criteria (issue #633, workbook-2 · per-role monthly budget caps)

| Criterion | Where |
|---|---|
| Caps resolved by **consuming** the workbook-1 persona/org-chart fields (never redefined) | `budget.load_role_budgets()` reads `registry/personas/org-chart.yaml` + the bound cards and asserts agreement; `budgets.yaml` `roles:` documents the vocabulary and the cap source |
| The chooser consults the per-role budget **before** any model call for that persona; caps CEO 300 / CTO 250 / COO 100 / CFO 50 / CMO 200 | `chooser._check_role_budget()` runs before `_check_budget()`; `tests/test_role_budget.py` asserts the caps equal the declaration |
| stop/warn/fallback applies **per role**; a role at cap refuses spend and records a metered decision | `RoleBudget.decide()` + `RoleBudgetBlocked` + `chooser._record_role_refusal()` (`RefusalRecord`, scope `role`) |
| CFO enforcement is deterministic — "zero-token arithmetic", no generative loop in the cap path | `RoleBudget.decide()` is a pure percentage comparison; `test_role_enforcer_is_deterministic_and_token_free` bans `random`/`time.`/`subprocess`/… in its source |
| `budgets.yaml` documents the per-role vocabulary; tests include a **mutation-proved** refusal | `budgets.yaml` `roles:` block; `test_role_cap_refusal_is_mutation_proved` disables the hard-cap comparison then the policy mapping and asserts the refusal probes die |

## One tiers.yaml judges every runtime's spawn (issue #1274)

Before this issue, `tiers.yaml` only gated the fleet's own model call
(`chooser.py`) — a Claude subagent dispatch or a hermes persona render could
still be handed a tier the table forbids for its task class, because nothing
re-checked them against the same window. `governance/spawn/tiering.py` closes
that gap: it is a pure judge (`judge(role, task_class, tier)`) that reads
*only* this file (via `loader.load_tier_table`, never a second copy of the
ladder) and refuses `FINOPS-ROLE-NOT-ALLOWED` for a fleet spawn, a
`claude-subagent`, or a `hermes-persona` alike, whenever the requested tier
sits outside the task class's `defaultTier..maxTier` window (floored at
`security.floorTier` for guarded classes). Run it directly with
`python3 -m governance.spawn.tiering judge --role R --class C --tier T`; the
control is proven in `scripts/check-tier-parity.sh`. `integrations/hermes/mapping.py`
exposes the consumer hook (`judge_persona_tier`); wiring it into the Claude
subagent dispatch render (`governance/spawn/render.py`) is a follow-up owned
by another lane this wave (see the PR's "Wiring needed" section).

## Provenance

Cannibalized and adapted from fleet sources (see
[`docs/CANNIBALIZATION.md`](../../docs/CANNIBALIZATION.md) doctrine):

- `shared-frontend` `docs/MODEL-PROFILES.md` — L0/L1/L2 ladder + FinOps
  guardrails (canonical policy: cheapest-capable wins, security ≥ L1,
  advisor-only top tier).
- `leaderboard` `scripts/elite/finops-router.sh`,
  `scripts/ops/finops-governor.sh`, `config/finops-budget.yaml` — per-tier
  budgets, throttle/degrade/block pre-flight (0/1/2), model-call audit JSONL.
- `hermes-agents` `models/model_tiering.py`,
  `services/model_tier_selector.py`, `services/escalation_handler.py`,
  `services/complexity_scorer.py` — tier config, escalation rules, complexity
  scoring.
- `capital-underwriting` `scripts/agent/lib/finops-router.sh` — tier→model
  routing + JSONL model-call audit.
- `CMR` `docs/MODEL-PROFILES.md` + `docs/decision-records/ADR-0022-finops-doctrine.md`
  — frontloading-is-policy and LOW/MED/HIGH/MAX ladder vocabulary.
- `kushin77/agent-orchestrator` issue #633 (workbook-2 · Pillar 2 budget
  enforcement) — per-role monthly caps CONSUMED from the workbook-1 declaration
  (`registry/personas/org-chart.yaml`, issue #632); the workbook mechanical
  rule "zero-token arithmetic" is honoured by keeping the cap path a pure
  percentage comparison with no model call.
