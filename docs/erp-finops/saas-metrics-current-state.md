# SaaS metrics — current state (MRR/ARR, cloud compute burn, invoicing bottlenecks, silo map)

Owner lane: **`docs/erp-finops/saas-metrics-current-state.md`** (issue `kushin77/agent-orchestrator#668`, child of EPIC #665 *ERPNext & DeepSeek FinOps*). One issue = one lane; this file is created fresh in the `docs/erp-finops/` directory and edits no existing file and no sibling lane's file.

This is a **current-state** document: it records only what exists in-repo today (measured against the checkout), and marks everything else `CANNOT-ASSESS`. Nothing here is a forward-looking target or a design decision.

## Summary

The repo already measures **cloud compute burn** (AI model spend) end-to-end at the *estimate* level — model-tier routing (`gateway/finops`), per-call cost estimation from point-in-time rate cards (`telemetry/metering`), per-tenant budget/quota enforcement (`telemetry/budgets`), a cost SLO template (`telemetry/observability`), and a per-event `costUsd` field on the tamper-evident ledger (`telemetry/ledger`). What it does **not** measure today is the *money that comes in*: there is no MRR, ARR, subscription, or recurring-revenue collection anywhere in-repo, and there is no invoicing or billing surface — the chargeback generator (`telemetry/budgets/chargeback.py`) is the closest artifact and it is an offline formatter over seed metering data, not a billing system.

## 1. MRR / ARR tracking

**Status: `CANNOT-ASSESS (no in-repo MRR/ARR collection — needs operator CRM/billing input).`**

A whole-tree search for `MRR`, `ARR`, `recurring revenue`, `subscription`, and revenue-shaped identifiers returns **zero** product hits (the only `ARR`/`carry` matches are the word "carry" in `guardrails/isolation/**` docstrings and test names — unrelated). Concretely, nothing in-repo:

- defines a subscription, plan-price, or seat-count record shape;
- computes monthly or annual recurring revenue;
- reads customer/CRM data (there is no CRM module; the ERPNext-facing lanes are `Blocked-by #645` per the epic's scope discipline);
- links any cost figure to a revenue figure.

Because MRR/ARR depend entirely on the operator's CRM/billing system (external to this repo), the honest reading is `CANNOT-ASSESS`, not `0`. There is no in-repo signal from which a number could be derived.

## 2. Cloud compute burn (AI model spend)

This is the one SaaS cost dimension that **is** collected in-repo, at the *estimated-cost* level. Per-surface:

### 2.1 `gateway/finops` — model chooser (issue #17)

`gateway/finops/budgets.yaml` seeds per-tenant monthly budgets with a `defaultPolicy: warn` and three seed tenants:

| Tenant | Monthly budget (USD) | Policy | warnAtPct | hardCapPct |
|---|---|---|---|---|
| `tenant-acme` | 100.00 | `fallback` | 80 | 100 |
| `tenant-beta` | 50.00 | `stop` | 80 | 100 |
| `tenant-gamma` | 25.00 | `warn` | 85 | 100 |

`gateway/finops/tiers.yaml` declares the L0/L1/L2 ladder with `costPerMTok` values (USD per million tokens, cheapest-first, "default illustrative configuration" per the file's own header):

- L0: `deepseek-v4-flash` 0.21 (primary), `gemini-2.5-flash` 0.30 (fallback)
- L1: `deepseek-v4-pro` 0.28 (primary), `claude-sonnet-4-5` 3.00 (fallback)
- L2: `deepseek-v4-pro-thinking` 0.46 (primary), `claude-opus-4-1` 15.00 (fallback)

Security floor `L1`; escalation thresholds `L0: 40.0`, `L1: 70.0`. The chooser emits a `CallRecord` (with `estimated_cost_usd` and `budget_action`) to a `MeteringSink` on every non-blocked choice.

### 2.2 `telemetry/metering` — usage metering + cost engine (issue #33)

The cost engine computes `costUsd` per call from **YAML rate cards** (point-in-time list prices, *cost estimation only — not live pricing, not customer billing*). Measured card values (USD per 1M tokens, input/output):

| Provider | Model | input | output |
|---|---|---|---|
| deepseek | `deepseek-chat` | 0.27 | 1.10 |
| deepseek | `deepseek-reasoner` | 0.55 | 2.19 |
| anthropic | `claude-haiku-4-5` | 1.00 | 5.00 |
| anthropic | `claude-sonnet-4-5` | 3.00 | 15.00 |
| anthropic | `claude-opus-4-5` | 15.00 | 75.00 |
| gemini | `gemini-2.5-flash` | 0.30 | 2.50 |
| gemini | `gemini-2.5-pro` | 1.25 | 10.00 |
| gemini | `gemini-2.5-pro` (longContext, ≥200k tokens) | 2.50 | 15.00 |

Cost resolution is **fail-closed**: an unknown model/rate returns `None` (never `0`); an unmetered call surfaces as `unmeteredCalls` and is never silently priced as zero. Daily + monthly rollups aggregate per tenant/agent/model. `telemetry/metering/config/budgets.yaml` seeds daily token budgets with `defaultMode: observe` (acme `2,000,000` observe; globex `1,000,000` enforce; tenant-beta `500,000` enforce).

### 2.3 `telemetry/budgets` — budgets, quotas, kill switch, chargeback (issue #34)

`config/policies.yaml` (`defaultMode: observe`) seeds per-tenant cost/token limits with a warn→block ladder over the durable metering feed:

- `tenant-omega` observe: cost `500.0` USD/mo (warnAtPct 0.8), tokens `5,000,000`, anthropic vendor cap `200.0`
- `acme` enforce: cost `120.0` USD/mo, tokens `2,000,000`, anthropic cap `50.0`
- `globex` enforce: cost `250.0` USD/mo (warnAtPct 0.7), tokens `1,000,000`, openai cap `100.0`

`config/quotas.yaml` declares per-plan soft/hard quotas (`free`/`standard`/`premium`/`enterprise`) over requests/tokens/concurrency/storage, e.g. `free` requests `100/500` per day, tokens `100,000/500,000`, storage `1 GiB / 5 GiB`. `config/killswitch.yaml` ships `globalPause: false` (the global spend halt). `chargeback.py` (`ChargebackReportGenerator`) emits per-tenant chargeback lines (`tenantId`, `month`, `calls`, `cacheHits`, `inputTokens`, `outputTokens`, `costUsd`, `unmeteredCalls`) as CSV/JSON — an honest sum over metering rollups, never a fabricated figure.

### 2.4 `telemetry/observability` — cost SLO (issue #32)

`slo_templates/cost-budget.yaml` is a per-tenant cost SLO template: `sloKind: cost`, `windowSeconds: 86400`, `budgetUsd: 25.0`, `severity: warning` — total recorded estimated cost over the window must not exceed `budgetUsd`.

### 2.5 `telemetry/ledger` — tamper-evident audit ledger (issue #31)

Every agent action/policy decision is recorded on a per-tenant append-only, SHA-256-chained ledger, with a `costUsd` field ("who did what, under whose policy, at what cost"). This is the *evidence* layer for cost, not the aggregation layer.

### Cloud burn assessment

**Measured:** per-tenant/per-agent/per-model *estimated* spend, from routing decision → rate-card cost → durable rollup → budget/quota enforcement → chargeback lines → SLO. **`CANNOT-ASSESS`:** the *actual* provider invoice and the *reconciled* bill. Rate cards are dated point-in-time list prices with "deliberately no live-pricing fetch" (`telemetry/metering/README.md`), so the delta between estimated cost and the real provider bill is unmeasured in-repo.

## 3. Manual invoicing bottlenecks

**Status: no invoicing surface exists in-repo — each bottleneck below is `CANNOT-ASSESS` on cost, with the implication sourced from the current state.**

The only "billing"-adjacent artifacts are (a) DLP test fixtures with fake URLs (`guardrails/dlp/tests/support.py`, `https://payments.internal:8443/billing`), (b) comments in `gateway/finops/budget.py`/`budgets.yaml` about "wiring real per-tenant billing state", and (c) the `telemetry/budgets/chargeback.py` generator "for tenant billing" — which is an offline CSV/JSON formatter over seed metering data, not an invoice emitter. There is no invoice generation, no payment gateway, no dunning, no tax/line-item ledger, and no CRM→ledger handoff (the ERPNext lanes are `Blocked-by #645`). Consequently the manual-invoicing flow implied by the current state is:

| Implied bottleneck | What the current state shows | Cost |
|---|---|---|
| Chargeback → invoice translation is manual | `chargeback.py` emits chargeback lines (CSV/JSON) but nothing consumes them into an invoice; no invoice record shape exists | `CANNOT-ASSESS` |
| Estimate ≠ billed amount | Rate cards are cost *estimates*, reconciled to nothing in-repo | `CANNOT-ASSESS` |
| No revenue/customer dimension | No CRM or subscription data to key an invoice to a customer | `CANNOT-ASSESS` |
| No invoice lifecycle (draft→sent→paid) | No ledger/journal of invoices anywhere in-repo | `CANNOT-ASSESS` |
| Operator hand-off | The epic's PF-1-style acquisition→ledger flows are not yet written in-repo; the sibling `docs/erp-finops/current-state.md` (issue #666, the current-state money map) is the designated cross-ref and is not present in this lane's base | `CANNOT-ASSESS` |

## 4. Metric-flow silo map

For each metric, where the **acquisition** (front-end) data sits vs. where the **ledger** (back-end) data sits vs. the **gap**.

| Metric | Acquisition (front-end) | Ledger (back-end) | Gap |
|---|---|---|---|
| MRR | none in-repo | none in-repo | **Total** — CRM/billing is external; no front-end or ledger surface exists |
| ARR | none in-repo | none in-repo | **Total** — same as MRR |
| Subscription / seats | none in-repo (no CRM module; ERPNext lanes `Blocked-by #645`) | none in-repo | **Total** |
| AI compute burn (cost) | `gateway/finops` chooser emits `CallRecord.estimated_cost_usd`; `telemetry/metering` rate-card `costUsd` | `telemetry/metering` store rollups → `telemetry/budgets` budget/chargeback (`costUsd`); `telemetry/ledger` `costUsd` per event | **Estimate vs. bill** — point-in-time list prices, no live pricing, no reconciliation to provider invoice |
| Token/call usage | `telemetry/metering` intake (gateway `ModelCallEvent`/`CallRecord`/`MeteringRecord` shapes) | `telemetry/metering` durable JSONL store + daily/monthly rollups | Minimal — usage is measured; only the cost conversion is an estimate |
| Budget/quota position | `gateway/finops` budget pre-flight; `telemetry/budgets` preflight rails | `telemetry/budgets` budget/quota state via `BudgetStateExporter`; audit JSONL | **Provisioning external** — seed config only; real tenant billing state is wired by the control plane (phase 7), not present in-repo |
| Chargeback / invoice | none (chargeback is a downstream formatter, not an acquisition surface) | `telemetry/budgets/chargeback.py` chargeback lines | **No invoice** — chargeback lines have no invoice consumer; invoicing is fully manual/external |
| Kill switch / spend halt | `gateway/finops` (stop policy) + `telemetry/budgets` killswitch (engaged/clear) | `telemetry/budgets` audit JSONL (refusal + pause/clear transitions) | Minimal — audited in-repo |

## Cross-references

- Sibling lane: `docs/erp-finops/current-state.md` (issue #666, *current-state money map*) — the designated money-map cross-ref; not present in this lane's base at the time this file was written. If it later lands, this file's §3 and §4 should be reconciled against it (this lane does not edit it).
- `docs/erp-finops/token-flow-baseline.md` (issue #667, sibling lane) — token-flow baseline + CRM hooks; referenced by name, not present in this base.
- In-repo surfaces cited: `gateway/finops/budgets.yaml`, `gateway/finops/tiers.yaml`, `telemetry/metering/config/budgets.yaml`, `telemetry/metering/rate_cards/*.yaml`, `telemetry/budgets/config/policies.yaml`, `telemetry/budgets/config/quotas.yaml`, `telemetry/budgets/config/killswitch.yaml`, `telemetry/budgets/chargeback.py`, `telemetry/observability/slo_templates/cost-budget.yaml`, `telemetry/ledger/README.md` (`costUsd` field), `fleet/telemetry.py` (per-run lifecycle — no cost dimension).

## CANNOT-ASSESS register

1. **MRR** — no in-repo collection (needs operator CRM/billing input).
2. **ARR** — no in-repo collection.
3. **Subscription / recurring revenue / seat count** — no in-repo collection.
4. **Manual invoicing cost and bottlenecks** — no invoicing surface in-repo; every implied bottleneck above is costed `CANNOT-ASSESS`.
5. **Actual provider bill / reconciled spend** — rate cards are point-in-time estimates with no live pricing and no reconciliation to a real invoice.
6. **Real deployed tenant billing state** — all budget/quota/budget figures above are *seed* config; runtime per-tenant billing is provisioned externally (control plane, phase 7).
