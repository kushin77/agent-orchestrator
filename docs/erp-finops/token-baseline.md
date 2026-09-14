# DeepSeek token-flow baseline (#667)

Parent epic: `#665` (ERPNext & DeepSeek FinOps). This doc is the **token-flow
baseline** deliverable: the per-call-class prompt-cache hit/miss/cost table, the
CANNOT-ASSESS items, and the in-repo CRM conversion-hook inventory. Companion
artifact: `gateway/finops/cache_baseline.py` (the offline script that prints the
table).

## Scope

DeepSeek bills prompt tokens in two buckets — **cache-hit tokens** (the shared
prefix served from DeepSeek's automatic prefix cache, at a discounted input
rate) and **cache-miss tokens** (new input, at the standard input rate) — plus
output tokens. This baseline aggregates usage **per call class** and reports:
requests, prompt tokens, hit tokens, miss tokens, cache-hit ratio, and cost.

## Honest-data contract

Verified against this checkout (not assumed):

- The repo's recorded usage shape — `gateway/providers` `Usage`,
  `telemetry/metering` `UsageRecord`, `gateway/finops` `CallRecord` — tracks
  only `input_tokens` and `output_tokens`. **No `prompt_cache_hit_tokens` /
  `prompt_cache_miss_tokens` (or any cache-token) field exists anywhere
  in-tree.** `telemetry/ledger` is a tamper-evident audit ledger, not a
  token-flow metering ledger, and carries no cache-token split either.
- Therefore a real-data join can price prompt + output from the rate cards but
  **cannot split prompt into hit vs miss**. The hit/miss/ratio/cache-adjusted-
  cost columns are `CANNOT-ASSESS` on any real recorded usage today.
- The table below is produced from the script's **bundled synthetic sample**
  (clearly labeled), because no real recorded DeepSeek cache-token flow exists
  in-tree to join.

## Measured table

`python3 gateway/finops/cache_baseline.py` (bundled synthetic sample — no real
recorded DeepSeek cache-token flow exists in-tree):

| call_class             | requests | prompt_tokens | hit_tokens | miss_tokens | cache_hit_ratio | cost_std_usd | cost_cached_usd |
|------------------------|---------:|--------------:|-----------:|------------:|----------------:|-------------:|----------------:|
| architecture-decision  |       40 |        24,000 |      2,400 |      21,600 |           10.0% |     0.048240 |        0.047256 |
| classify-route         |    1,200 |       180,000 |    126,000 |      54,000 |           70.0% |     0.101400 |        0.076200 |
| code-author            |      400 |       320,000 |    128,000 |     192,000 |           40.0% |     0.262400 |        0.236800 |
| code-review            |      200 |       160,000 |     48,000 |     112,000 |           30.0% |     0.131200 |        0.121600 |
| docs-authoring         |      600 |       240,000 |    120,000 |     120,000 |           50.0% |     0.262800 |        0.238800 |
| memory-ops             |      800 |        64,000 |     51,200 |      12,800 |           80.0% |     0.034880 |        0.024640 |
| research               |      100 |        50,000 |     10,000 |      40,000 |           20.0% |     0.093200 |        0.089100 |
| test-run               |      300 |        90,000 |     45,000 |      45,000 |           50.0% |     0.090300 |        0.081300 |
| **TOTAL**              | **3,640** | **1,128,000** | **530,600** | **597,400** |         **47.0%** |    **1.024420** |       **0.915696** |

Cost conventions:

- `cost_std_usd` = `prompt × input + output × output`, from the repo rate cards
  (`telemetry/metering/rate_cards/deepseek.yaml`) — **no cache discount**.
- `cost_cached_usd` = `hit × cache-hit-input + miss × input + output × output`;
  the cache-hit input rate is a point-in-time DeepSeek baseline constant
  (below), **not** in the repo rate card.

At the sample's aggregate 47.0% cache-hit ratio, the cache-adjusted cost is
~10.6% below the standard cost ($0.9157 vs $1.0244). Mechanical/classification
classes (`memory-ops` 80%, `classify-route` 70%) show the highest hit ratios;
research/architecture (`research` 20%, `architecture-decision` 10%) the lowest —
consistent with prefix reuse being highest where a long, stable system block
dominates the prompt.

## CANNOT-ASSESS items

| Item | Why CANNOT-ASSESS | Unblocked by |
|------|-------------------|--------------|
| Real hit/miss token split (per call class) | No recorded usage carries `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` | Recording DeepSeek's `usage.prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` in the provider `Usage` → `UsageRecord` shape |
| Real cache-hit ratio | Derived from the missing split | Same |
| Real cache-adjusted cost | Depends on the missing split + a cache-hit rate absent from the rate card | Same + declaring the cache-hit input tier in `deepseek.yaml` |
| Cache-hit input list price | Not declared in the repo rate card; the script uses a point-in-time baseline constant | Re-verify against DeepSeek current pricing and add a cache tier to the card |
| External CRM conversion hooks (Salesforce/HubSpot/etc.) | No external CRM integration exists in-tree | Future CRM→ERPNext webhook lane (#671, Blocked-by #645) |

## Rate cards vs. baseline cache-hit assumption

The repo's `deepseek.yaml` declares only standard input/output (USD / 1M):

- `deepseek-chat`: input 0.27, output 1.10
- `deepseek-reasoner`: input 0.55, output 2.19

The script's cache-hit input rates are point-in-time DeepSeek list prices held
as a **documented baseline constant** (`deepseek-chat` 0.07, `deepseek-reasoner`
0.14), flagged in the script docstring as NOT in the rate card and requiring
re-verification before any billing use.

## CRM conversion-hook inventory

Hooks that **exist in-repo** (the control plane's own signup/entitlement path):

| Hook | Where | What it records |
|------|-------|-----------------|
| Tenant lifecycle | `identity/onboarding/model.py` | `provisioning → active → suspended`; IdP mapping; seed-pack install; tenant types `platform/startup/smb/enterprise` |
| Plan → entitlement → RBAC | `identity/entitlements/` (catalog + engine) | commercial plan (`pro` etc.) → feature/limit entitlements → RBAC grants |
| Subscription status | `portal/server/state.py` (`Tenant.subscription_status`, `plan`) | `active` / `trial` status per tenant; `plan` defaults to `startup` |
| Trial pause (approval-gated) | `portal/server/app.py` | pausing a `trial` tenant emits a `trial hold` pending approval — a conversion/upgrade trigger point |
| Provisioning / activation audit | `portal/server/state.py`, `identity/cpapi` | `system:provision` / `registry.activate` events ("activated on onboarding"); Org-as-tenant + entitlements plan view |

Hooks that are **CANNOT-ASSESS** (external CRM): any Salesforce/HubSpot/CRM
webhook, lead-to-tenant handoff, or closed-won → provisioning trigger — none
exists in-tree; they belong to the future CRM→ERPNext webhook lane (#671,
Blocked-by #645), not to this baseline.
