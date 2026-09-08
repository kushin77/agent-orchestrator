# telemetry/metering — usage metering + cost engine (multi-provider rate cards)

> Owner lane: **telemetry** (issue `kushin77/agent-orchestrator#33`, work item 29,
> phase 5). Parent: EPIC-00 (issue #4). Doctrine:
> [`AGENTS.md`](../../AGENTS.md),
> [`docs/EXECUTION-PLAN.md`](../../docs/EXECUTION-PLAN.md),
> [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md),
> [`docs/GOLDEN-RULES.md`](../../docs/GOLDEN-RULES.md),
> [`docs/CANNIBALIZATION.md`](../../docs/CANNIBALIZATION.md).

This tree is the **usage metering + cost engine** of the telemetry pillar:
per-tenant token accounting and multi-provider cost estimation — the metering
the SaaS bills and FinOps reports on (EPIC-00, phase 5). Every model call is
recorded once with its tenant/agent/provider/model/route stamp, real token
counts, cache-hit flag and cost estimate; cost is computed from **YAML rate
cards** with long-context tiers; usage is aggregated **durably and
idempotently** into daily + monthly rollups per tenant/agent/model; and an
**observe→enforce** toggle governs per-tenant daily token budgets (safe
rollout).

This lane owns `telemetry/metering/**` only (one issue = one lane, per
[`docs/EXECUTION-PLAN.md`](../../docs/EXECUTION-PLAN.md)). It **consumes** —
and never redefines — the merged record vocabularies of the earlier lanes
that emit model-call records (see [Consumed vocabulary](#consumed-vocabulary)).

Everything here is **fully offline**: Python 3 stdlib + PyYAML only, no
network, no server, no npm/node.

## What this delivers (issue #33 acceptance criteria)

| Criterion | Where |
|-----------|-------|
| Usage records per call: tenant/agent/model/route, tokens in+out, cache-hit, cost-estimate | `model.py` (`UsageRecord`), `intake.py` |
| Multi-provider rate cards incl. context tiers; estimator returns **null on unknown (never 0)** | `ratecards.py`, `rate_cards/*.yaml` |
| Durable aggregation — no lost counts across instances | `store.py` (append-only JSONL; rollups re-scan the full log) |
| Daily + monthly usage rollups per tenant/agent/model; usage API for tenants + internal billing | `report.py` (`UsageReporter`) |
| Cost attribution (per-tenant, per-agent, provider mix) | `report.py`, `cli.py report` |
| Observe→enforce toggle: enforce daily token budgets when enabled (safe rollout) | `budget.py`, `config/budgets.yaml` |
| Idempotent ingest — replay never double-counts | `store.py` + `intake.py` (source-key dedup), negative tests |

## Consumed vocabulary (never redefined)

The intake accepts each merged record shape as a JSON dict (the durable feed
shape) and maps it onto the canonical `UsageRecord`:

| Source record | Emitted by | Key fields consumed |
|---|---|---|
| `ModelCallEvent` | `gateway/providers` (issue #15) | `provider`, `model`, `tenant_id`, `agent_id`, `logical_key`, `status`, `usage{input_tokens,output_tokens}`, `ts` |
| `CallRecord` | `gateway/finops` (issue #17) | `tenant_id`, `agent_id`, `task_class`, `tier`, `model`, `provider`, `estimated_cost_usd`, `budget_action`, `timestamp` |
| `MeteringRecord` | `gateway/limits` (issue #19) | `tenant`, `agent`, `model_tier`, `request_id`, `outcome`, `input_tokens`, `output_tokens`, `cached`, `zero_cost`, `at` |
| gateway proxy / observability record | `telemetry/observability` (issue #32, camelCase) | `requestId`, `tenantId`, `agentId`, `provider`, `model`, `tier`, `inputTokens`, `outputTokens`, `estimatedCostUsd`, `outcome`, `ts` |

Cache-hit semantics are the gateway/limits exception and are honored here:
a `MeteringRecord`/gateway record with `outcome=cache_hit` (and/or
`cached`/`zero_cost`) is the **only** metered record that is explicitly
zero-cost.

## Cost resolution doctrine (each branch is negative-tested)

1. **Cache hit** → `costUsd=0.0`, `costSource=cache_hit`, metered (the only
   explicit zero-cost path).
2. **Non-call outcome** (`blocked`, `denied`, `budget_exceeded`,
   `rate_limited`, `queued`, `degraded`, `refused`, finops `stop`) → a
   non-billable event; no usage, no cost, excluded from rollups.
3. **Local model** (rate-card `local: true`) → metered `0.0` from the card —
   a *priced* $0 (self-hosted), valid even without token counts.
4. **Live call with token usage + a card entry** → cost from the rate card
   (`costSource=rate_card`); 0 tokens on a known card is an honest `0.0`.
5. **No card entry but a positive attached estimate** (a `CallRecord` /
   observability record the gateway already priced) → attributed as-is
   (`costSource=attached`).
6. **Anything else** → `metered=false` with an `unmeteredReason` and
   `costUsd=null` (**fail closed**). An unmetered call is never silently
   priced as zero.

Rollups and the billing feed count only **billable** records, and an
unmetered record's tokens are counted but its cost is **never** assumed to
be zero — the report surfaces `unmeteredCalls` separately.

## Rate cards

`rate_cards/*.yaml` — one file per provider, USD per 1,000,000 tokens
(input/output split, because every provider prices output several times
above input). A model may declare a `longContext` tier that both rates step
up to once the prompt/input token count exceeds `longContextThresholdTokens`
(the Gemini/Vertex convention; `gemini-2.5-pro` ships a real example).

```yaml
schemaVersion: 1
provider: gemini
models:
  gemini-2.5-pro:
    standard:
      inputUsdPerMillion: 1.25
      outputUsdPerMillion: 10.00
    longContext:
      inputUsdPerMillion: 2.50
      outputUsdPerMillion: 15.00
    longContextThresholdTokens: 200000
```

Cards are **POINT-IN-TIME list prices for cost estimation only** — not live
pricing, not customer billing. Providers change prices without notice; the
figures are dated in the card files and need periodic review. There is
deliberately no live-pricing fetch: cost figures must stay deterministic and
reconcilable against a specific historical rate.

## Module layout

| File | Purpose |
|------|---------|
| `model.py` | Canonical `UsageRecord` + source/cost-source vocabulary + day/month bucket helpers. |
| `ratecards.py` | `RateCardStore`, YAML loader + schema validation (fail closed), `estimate()` → `None` on unknown. |
| `rate_cards/*.yaml` | Per-provider rate cards (anthropic, deepseek, gemini, ollama, openai). |
| `intake.py` | `MeteringIntake`: source-vocabulary detection, normalization, cost resolution, idempotent `ingest()`. |
| `store.py` | `MemoryUsageStore` / `JsonlUsageStore` — durable append-only JSONL + source-key dedup. |
| `report.py` | `UsageReporter`: daily/monthly rollups, per-tenant/agent/model cost attribution, provider mix, billing feed. |
| `budget.py` | `DailyTokenBudget` — observe→enforce daily token budget toggle. |
| `config/budgets.yaml` | Default budget policies (all `observe` except the explicitly-flipped examples). |
| `cli.py` | Offline operator CLI (`python3 -m telemetry.metering.cli …`). |
| `tests/` | pytest suite incl. the fail-closed and idempotency negatives. |

## Usage

All commands run from the repo root (offline):

```bash
# Price one call from the rate cards (exit 2 = unmetered/unknown)
python3 -m telemetry.metering.cli estimate --provider gemini \
    --model gemini-2.5-pro --input 250000 --output 5000
python3 -m telemetry.metering.cli estimate --provider futureco --model future-model-x

# Dump the loaded rate cards
python3 -m telemetry.metering.cli cards

# Ingest a JSONL feed of merged model-call records (idempotent replay)
python3 -m telemetry.metering.cli ingest --feed records.jsonl --store /tmp/usage.jsonl
python3 -m telemetry.metering.cli ingest --feed records.jsonl --store /tmp/usage.jsonl  # replay: 0 new

# Cost-attribution report (per-tenant / per-agent / provider mix / billing)
python3 -m telemetry.metering.cli report --store /tmp/usage.jsonl

# Daily token budget check (observe vs enforce tenants)
python3 -m telemetry.metering.cli budget --tenant acme   --store /tmp/usage.jsonl
python3 -m telemetry.metering.cli budget --tenant globex --store /tmp/usage.jsonl

# End-to-end evidence walk-through (intake -> store -> report -> budget)
python3 -m telemetry.metering.cli demo
```

Python API:

```python
from pathlib import Path
from telemetry.metering.intake import MeteringIntake
from telemetry.metering.store import JsonlUsageStore
from telemetry.metering.report import UsageReporter

store = JsonlUsageStore(Path("/tmp/usage.jsonl"))
intake = MeteringIntake(store=store)

event = {  # a gateway/providers ModelCallEvent dict (issue #15 shape)
    "provider": "gemini", "model": "gemini-2.5-flash",
    "tenant_id": "acme", "agent_id": "coder-1", "logical_key": "MED",
    "status": "success",
    "usage": {"input_tokens": 1000, "output_tokens": 500, "tokens": 1500},
    "ts": "2026-09-08T10:00:00Z",
}
outcome = intake.ingest(event)          # outcome.record.metered, .duplicate
reporter = UsageReporter(store)
print(reporter.totals().to_dict())      # durable aggregation / billing feed
print(reporter.tenant_daily("acme"))    # tenant usage API
```

## Verification

```bash
python3 -m pytest telemetry/metering/tests -q -p no:cacheprovider
python3 -m telemetry.metering.cli demo
make verify       # repo gate stays green (run from the worktree root)
```

## Provenance (cannibalized sources)

All sources verified present under the fleet `.research/` tree
(`/home/akushnir/agent-orchestrator/.research/`, gitignored):

| Source (repo/path) | What was adapted |
|---|---|
| `capital-underwriting` `apps/server/src/lib/aiCostRates.ts` | Rate-card shape ($/1M input+output split), long-context tier + threshold, fractional-cost estimator returning **null on unknown — never 0**, and the no-rounding-until-the-total-leaves-tracking note. |
| `capital-underwriting` `apps/server/src/lib/aiUsage.ts` | Per-tenant accounting + daily-token budget with an **observe→enforce** toggle and durable cross-instance SUM over an append-only ledger (`MAX(local, durable)` convergence); the point-in-time Gemini/Anthropic rates behind `rate_cards/gemini.yaml` + `anthropic.yaml`. |
| `ollama` `ollama/api/routes/usage.py` | Per-entity usage/cost accounting + daily-breakdown reporting shape behind `UsageReporter.tenant_daily`. |
| `leaderboard` `scripts/finops/cost-attribution.sh` | Per-tenant/per-agent cost attribution + provider-mix reporting shape. |
| `CMR` `fleet/cost-report.sh` | Cost report / billing-feed aggregation conventions. |
| `shared-temporal` `governance/cost-tracking.ts` (issue body) | Run-cost accounting + durable aggregation intent. |
