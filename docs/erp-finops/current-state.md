# Current-state money map — subscription flows + manual reconciliation points (PF-1)

> **Issue:** `kushin77/agent-orchestrator#666` (PF-1, child of EPIC #665 —
> "ERPNext & DeepSeek FinOps — token-optimal subscription engine from CRM hook
> to ledger"). **Lane:** `finops-map`. **Class:** elite / gdc:enterprise.
> **Pillar:** observability-finops (phase 5).
>
> This file is the **Phase-1 current-state money map**: every multi-product
> subscription flow and every manual data-reconciliation point across the
> fleet's infrastructure, with each silo named, its owner surface cited, and
> what breaks when it loses sync. It is a **map of the money as it exists in
> this repository today** — not a design, and not a prediction.

---

## 1. Scope note — what is measured vs `CANNOT-ASSESS`

This document is governed by the elite honesty rule: **measure what is
measurable in-repo, and never invent what is not.** Two disjoint sets result.

### 1.1 Measured in-repo (real files, real surfaces)

The following money surfaces exist as committed code/config and are cited
with their paths:

| Surface | Path(s) | What it owns |
|---|---|---|
| Tenant plan / subscription catalog | `identity/entitlements/plans/catalog.yaml`, `identity/entitlements/engine.py` | `free`/`pro`/`enterprise` plans; `subscription_status` (`active`/`inactive`) |
| Per-tenant model-tier + budget chooser | `gateway/finops/tiers.yaml`, `budgets.yaml`, `chooser.py`, `budget.py` | L0/L1/L2 cheapest-capable ladder; per-tenant budgets (`stop`/`warn`/`fallback`) |
| Multi-provider call record | `gateway/providers/events.py` (per-provider adapters) | `ModelCallEvent` with `usage{input_tokens,output_tokens}` |
| Cost/capacity controls (incl. zero-cost cache) | `gateway/limits/cache.py`, `budget.py`, `ratelimit.py`, `backpressure.py` | semantic cache (`outcome=cache_hit, zero_cost=True`), token budget, backpressure |
| Usage metering + cost engine | `telemetry/metering/model.py`, `ratecards.py`, `rate_cards/*.yaml`, `intake.py`, `store.py`, `report.py`, `cost_details.py` | `UsageRecord`; **point-in-time rate cards**; daily/monthly rollups; billing feed |
| Budgets / quotas / kill switch / chargeback | `telemetry/budgets/budget.py`, `quota.py`, `killswitch.py`, `chargeback.py`, `exporter.py`, `alerts.py` | warn→block ladder; per-tenant chargeback lines; SLO/budget state export |
| Tamper-evident audit ledger | `telemetry/ledger/` (`schema.py`, `store.py`, `audit_event.schema.json`) | hash-chained per-tenant events carrying a `costUsd` field |
| Audit read model | `telemetry/audit/read_model.py` | deterministic, read-only read model over the ledger |
| Observability usage/chargeback + cost SLOs | `telemetry/observability/usage.py`, `slos.py`, `slo_templates/`, `breach.py` | per-tenant usage feed; cost SLO with `budget_usd` |
| Chat per-turn FinOps | `telemetry/chat/attribution.py`, `budget_guard.py`, `cache_accounting.py`, `tiering.py`, `readmodel.py` | one attribution per turn; prompt-cache cost accounting |
| Fleet brain directive FinOps | `governance/finops/policy.json`, `chooser.py`; `fleet/directive.json` | `flash`/`pro`/`auditor` fleet tiers; brain-issued spend directives |

### 1.2 `CANNOT-ASSESS` (external — needs operator input)

The following are **business-data systems that live outside this repository**.
None of their data is observable in the tree, so no number is stated for any
of them:

1. **CRM system** — the customer-acquisition / conversion funnel that feeds
   new subscriptions. No CRM schema, webhook, or export exists in-repo.
2. **Invoicing tool** — the system that turns chargeback output into real
   customer invoices. In-repo, `ChargebackReportGenerator` *emits* lines; it
   does not invoice.
3. **Cloud-provider billing** — the actual invoices from Anthropic / Gemini /
   DeepSeek / OpenAI (the real cash out). In-repo there is only *estimated*
   cost from rate cards, never an actual-invoice feed.
4. **Payment processor** (card charges, dunning, churn) — the source of truth
   for whether a tenant has actually paid. `subscription_status` is a local
   field, not a payment-state feed.
5. **ERPNext financial ledger** — the target *system of record* for EPIC #665
   (see PF-4, #669). It does not exist in this repo today; the CRM→ERPNext
   webhook bridge (PF-6, #671) is blocked-by #645 and not yet present.

> **Rule applied throughout:** every reconciliation point that depends on any
> of the five systems above is marked `CANNOT-ASSESS` for its cost/frequency
> where the repo gives no evidence, and its automatable replacement is named
> against the PF-* ladder rather than invented.

---

## 2. Subscription flows (multi-product)

The platform runs **four distinct money-bearing flows**, each with its own
authority surface. "Multi-product" means both the *tenant plans* sold and the
*per-provider model subscriptions* the platform consumes and passes through.

### 2.1 Tenant plan acquisition (free → pro → enterprise)

- **Authority:** `identity/entitlements/` — `plans/catalog.yaml` declares the
  plans; `engine.py` resolves the `subscription_status` and the plan-gated
  capability set; `overrides.py` grants time-boxed departures.
- **Money move:** a tenant's *plan* determines its entitlements, which the
  budget/quota rails then enforce (`telemetry/budgets/quota.py` resolves
  plan-default limits).
- **Sync hazard:** `subscription_status` is a **local** field. Whether the
  tenant actually paid is external (payment processor / CRM). See
  reconciliation point **R3**.

### 2.2 Per-tenant model-usage pass-through (per provider)

- **Authority:** `gateway/providers/` (call record) → `gateway/finops/`
  (tier + budget) → `gateway/limits/` (cache/rate/backpressure) →
  `telemetry/metering/` (usage + cost).
- **Money move:** each model call is attributed a **cost estimate** from the
  rate cards (`rate_cards/*.yaml`, one file per provider) and rolled into
  daily/monthly per-tenant aggregates (`report.py`).
- **Sync hazard:** the rate cards are **point-in-time list prices**, not live
  pricing — the real cash out arrives only on external cloud invoices. See
  reconciliation points **R1**, **R7**.

### 2.3 Fleet internal spend (the brain's own model spend)

- **Authority:** `governance/finops/` + `fleet/` — the brain issues a
  directive carrying a FinOps block (`model.tier` + `model.thinking`); the
  chooser converts it to a spawn record and refuses anything that is not the
  brain's choice.
- **Money move:** fleet tier vocabulary (`flash`/`pro`/`auditor`) is a
  **separate ladder** from the tenant ladder (`gateway/finops/tiers.yaml`
  L0/L1/L2). See reconciliation point **R6**.

### 2.4 Chat-surface per-turn spend

- **Authority:** `telemetry/chat/` — one `TurnAttribution` per turn, guarded
  before the model is called, with prompt-cache reuse accounted so a cache-hit
  turn is visibly cheaper than the same turn cold.
- **Money move:** the turn's cost comes from the metering rate cards over the
  *billable* (uncached) input; `cache_accounting.py` credits cached prefixes.
- **Sync hazard:** a cache report must be possible (`cached_tokens ≤ cacheable
  prefix`), so an over-credited prefix inflates the apparent savings. See
  reconciliation point **R8** (PF-8 owns the hit-vs-miss audit).

---

## 3. Reconciliation points (the load-bearing table)

Every point where a **manual** step currently holds two money surfaces in
agreement. "Who performs" and "cost" are measured only where the repo
evidences them; otherwise `CANNOT-ASSESS`.

| # | Reconciliation point | Two surfaces joined | Who performs | Cadence | Cost | Automatable replacement | What breaks on sync loss |
|---|---|---|---|---|---|---|---|
| R1 | Estimated cost vs actual cloud invoice (COGS) | `telemetry/metering/rate_cards/*.yaml` (list price) ↔ external cloud-billing invoices | Finance/operator | Monthly | `CANNOT-ASSESS` | PF-3 (SaaS metrics: cloud burn) + PF-11 (journal-posting discrepancies) → PF-4 (ERPNext system of record) | COGS mis-stated → wrong gross margin, wrong tenant chargeback |
| R2 | Chargeback output vs customer invoice | `telemetry/budgets/chargeback.py` (CSV/JSON) ↔ external invoicing tool | Finance/operator | Monthly (chargeback is month-bucketed) | `CANNOT-ASSESS` | PF-6 (CRM→ERPNext webhook bridge) | Tenant billed wrong amount or never billed |
| R3 | Local `subscription_status` vs actual payment state | `identity/entitlements/engine.py` (`active`/`inactive`) ↔ payment processor / CRM | Operator | Event-driven (payment event) | `CANNOT-ASSESS` | PF-6 (payment event → flip subscription) | Churned tenant keeps consuming metered quota (or paid tenant wrongly blocked) |
| R4 | Two cost aggregations (observability vs metering) | `telemetry/observability/usage.py` (`estimatedCostUsd` from spans) ↔ `telemetry/metering/report.py` (from `UsageRecord`) | Operator | Monthly | `CANNOT-ASSESS` | Audit join (CMR declared-vs-actual) / PF-11 | Silent double- or under-count in the billing feed |
| R5 | Metering rollup vs audit-ledger `costUsd` | `telemetry/metering/report.py` ↔ `telemetry/ledger` (`costUsd` per event) | Operator | Monthly | `CANNOT-ASSESS` | Audit join / PF-8 (hit-vs-miss vs billing queries) | Tamper-evident record disagrees with billing feed → audit finding |
| R6 | Fleet tier ladder vs tenant tier ladder | `governance/finops/policy.json` (`flash`/`pro`/`auditor`) ↔ `gateway/finops/tiers.yaml` (L0/L1/L2) | Operator | On ladder change | `CANNOT-ASSESS` | Gate-enforced vocabulary (`scripts/check-finops-chooser.sh`) + PF-2 (token-flow baseline) | A fleet directive runs tenant-side at a different cost tier → spend drift |
| R7 | Rate-card price drift | `rate_cards/*.yaml` (dated figures) ↔ provider list-price changes | Operator | Periodic (price change) | `CANNOT-ASSESS` | PF-2 (token-flow baseline) informs; PF-11 flags discrepancy | Stale price → wrong cost estimate → wrong budget decision |
| R8 | Cache-hit savings vs actual cached tokens | `telemetry/chat/cache_accounting.py` / `gateway/limits/cache.py` ↔ `engine/memory/prompt_cache.py` (prefix footprint) | Operator | On cache-policy change | `CANNOT-ASSESS` | PF-8 (cache-efficiency audit: hit vs miss tokens) | Over-credited prefix inflates apparent savings; under-credited hides cost |

> **Honesty note:** the "cadence" values that are *measured* are only those the
> repo itself implies (chargeback is month-bucketed; the rate cards are dated
> and "need periodic review"). All "cost" minutes are `CANNOT-ASSESS` because
> no operator-time telemetry exists in this tree, and no number is fabricated
> here.

---

## 4. Silo map — front-end acquisition vs back-end ledger

The brief requires the two halves separated explicitly. They are disjoint in
the tree today, and **nothing in-repo joins them automatically** — that is the
entire reconciliation surface of §3.

### 4.1 Front-end acquisition silos (money *in* — how usage/customers enter)

| Silo | Owner surface | Money role | Loses sync with → breaks |
|---|---|---|---|
| Plan/subscription catalog | `identity/entitlements/` | declares what a tenant is entitled to | R3 (payment reality) |
| Tenant onboarding | `identity/onboarding/` (`Org.id == Tenant.id`) | provisions the billable tenant identity | R3 |
| Model-tier + budget chooser | `gateway/finops/` | routes every call to the cheapest capable tier | R6 (fleet ladder) |
| Multi-provider gateway | `gateway/providers/` | emits the per-call record (tokens/provider/model) | R1 (actual invoice) |
| Cost/capacity controls | `gateway/limits/` | zero-cost cache, token budget, backpressure | R8 (cache truth) |
| Fleet brain FinOps | `governance/finops/` + `fleet/` | the platform's *own* spend, tier-gated by directive | R6 |

### 4.2 Back-end ledger silos (money *out* / *recorded* — how spend is tracked)

| Silo | Owner surface | Money role | Loses sync with → breaks |
|---|---|---|---|
| Usage metering + cost engine | `telemetry/metering/` | canonical usage + estimated cost, billing feed | R4, R5, R7 |
| Budgets / quotas / kill switch / chargeback | `telemetry/budgets/` | enforces limits; emits chargeback + state export | R2 |
| Tamper-evident audit ledger | `telemetry/ledger/` | per-tenant `costUsd` evidence chain | R5 |
| Audit read model | `telemetry/audit/` | read-only, filterable view of the ledger | R5 |
| Observability usage/chargeback + cost SLOs | `telemetry/observability/` | second usage feed + cost SLO budgets | R4 |
| Chat FinOps read model | `telemetry/chat/readmodel.py` | per-turn/per-conversation/per-ticket rollups | R8 |

### 4.3 External (neither silo, but every reconciliation terminates here)

CRM · invoicing tool · cloud-provider billing · payment processor · ERPNext.
All `CANNOT-ASSESS` (§1.2). Every automatable replacement below exists
precisely to stop the manual join between §4.1, §4.2 and these five systems.

---

## 5. Automatable-replacement map (PF-* → reconciliation point)

| PF id | Issue | Replacement | Replaces (manual step) |
|---|---|---|---|
| PF-2 | #667 | DeepSeek token-flow baseline + CRM conversion-hook inventory | the baseline that R6/R7 currently eyeball |
| PF-3 | #668 | SaaS metrics (MRR/ARR, cloud burn, invoicing bottlenecks) + silo map | R1 (cloud burn vs list price) |
| PF-4 | #669 | ADR: ERPNext as financial system of record | the *destination* ledger every join targets |
| PF-6 | #671 | CRM→ERPNext webhook bridges (conversion events → accounting modules) | R2 (chargeback→invoice) and R3 (payment→subscription) |
| PF-8 | #673 | Cache-efficiency audit (hit vs miss tokens vs ERPNext billing queries) | R8 (cache savings) and R5 (hit-vs-miss join) |
| PF-10 | #675 | E2E sandbox simulation (sign-up → ERPNext billing → revenue recognition) | the whole §3 chain, end-to-end |
| PF-11 | #676 | Compliance audit + journal-posting edge cases (latency, discrepancies, security) | R1/R4/R7 discrepancy surfacing |

> The CMR **audit join** (declared ∩ actual, see `vendor/CMR/docs/ACCESS.md`
> §4 and `vendor/CMR/docs/IDENTITY.md` §5) is the general pattern behind the
> R4/R5 joins; the PF-* items above are its ERPNext/DeepSeek-specific
> incarnations in this epic.

---

## 6. What is true *today* (the honest summary)

- The platform **meters and prices** usage end-to-end *internally*
  (`gateway/*` → `telemetry/metering` → `telemetry/budgets` →
  `telemetry/ledger`), and every cost figure it produces is **estimated**,
  never actual cash.
- There is **no in-repo join** between the acquisition silos (§4.1), the
  ledger silos (§4.2), and the five external systems (§4.3). Every boundary
  between them is a **manual reconciliation** (the eight points in §3).
- The epic's automation ladder (PF-2…PF-11) exists to replace those manual
  joins; none of it is implemented in this repo yet — this map is its
  Phase-1 input.
