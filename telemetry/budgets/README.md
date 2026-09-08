# telemetry/budgets — per-tenant budgets + quotas + global kill switch + SLO export

> Owner lane: **telemetry** (issue `kushin77/agent-orchestrator#34`, work item 30,
> phase 5). Parent: EPIC-00 (issue #4). Doctrine:
> [`AGENTS.md`](../../AGENTS.md),
> [`docs/EXECUTION-PLAN.md`](../../docs/EXECUTION-PLAN.md),
> [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md),
> [`docs/GOLDEN-RULES.md`](../../docs/GOLDEN-RULES.md),
> [`docs/CANNIBALIZATION.md`](../../docs/CANNIBALIZATION.md).

This tree is the **operational safety-rail contract** of the telemetry pillar
for the multi-tenant AI SaaS (EPIC-00, phase 5): per-tenant **soft/hard
quotas** (calls, tokens, concurrency, storage), per-tenant + per-vendor/model
**budget enforcement** with a warn → block ladder over the durable metering
feed (issue #33), a platform-wide **global kill switch** that halts all
non-critical billable model calls instantly, durable **audit** of every
BLOCK/WARN/refusal decision, a machine-readable **SLO/budget state exporter**
for tenant dashboards and alerting, and a per-tenant **chargeback report
generator** for billing.

This lane owns `telemetry/budgets/**` only (one issue = one lane, per
[`docs/EXECUTION-PLAN.md`](../../docs/EXECUTION-PLAN.md)). It **consumes** —
and never redefines — the merged record vocabularies of the earlier lanes
(see [Consumed vocabulary](#consumed-vocabulary)).

Everything here is **fully offline**: Python 3 stdlib + PyYAML only, no
network, no server, no npm/node.

## What this delivers (issue #34 acceptance criteria)

| Criterion | Where |
|-----------|-------|
| Per-tenant soft/hard quota enforcement at gateway/engine (calls, tokens, concurrency, storage) | `quota.py` (`QuotaEnforcer`) + `preflight.py` |
| Budget enforcer per vendor/model with warn→block ladder; global pause switch (kill all tenant agent activity) with audit | `budget.py` (`BudgetEnforcer`), `killswitch.py` (`KillSwitchController`), `audit.py` |
| SLO exporter feeding tenant dashboards; chargeback report generator for tenant billing | `exporter.py` (`BudgetStateExporter`), `chargeback.py` (`ChargebackReportGenerator`) |
| Quota overrides per plan (entitlements link, Phase 6) | `quota.py` plan-default resolution, `config/quotas.yaml` |
| Audit of budget decisions (BLOCK/WARN events) | `audit.py` (`BudgetAuditStore`, `JsonlAuditStore`) |

## Consumed vocabulary (never redefined)

| Vocabulary | Source | How this lane consumes it |
|---|---|---|
| Decision ladder `allow → warn → block` + `warnAtPct` thresholds | `gateway/finops` (issue #17) | the budget ladder and warn/block thresholds in `budget.py` |
| Non-billable outcomes `budget_exceeded` / `blocked` / `refused` | `telemetry/metering` (issue #33) | a refused call maps onto one of these, so it is never metered as usage |
| Durable daily/monthly spend per tenant/vendor | `telemetry/metering` (issue #33) | read through `ledger.py` (`MeteringReporterLedger`), never a per-process counter |
| Observe → enforce rollout toggle | `telemetry/metering` (issue #33) | budget mode in `budget.py` — new blocking controls default `observe` |
| SLO kinds + verdicts (`OK`/`AT_RISK`/`BREACHED`/`NO_DATA`), `SloResult` | `telemetry/observability` (issue #32) | `exporter.py` renders the SLO feed verbatim (no re-evaluation, no redefinition) |
| Org-as-tenant model | `identity/rbac` (issue #12) | every check is keyed by `tenant_id` |

## The three rails (pre-dispatch enforcement order)

The gateway/engine lanes call [`preflight.py`](preflight.py) before a model
call; it runs the rails in a safe order and returns one verdict:

1. **Kill switch** (`killswitch.py`) — while engaged, every non-critical call
   is refused **first** (spend stops instantly regardless of headroom).
2. **Quota** (`quota.py`) — soft limits warn, hard limits refuse, over
   calls/tokens/concurrency/storage.
3. **Budget** (`budget.py`) — per-tenant (and per-vendor/model) cost/token
   limits over the durable metering feed with the warn → block ladder.

```python
from telemetry.budgets.budget import BudgetEnforcer
from telemetry.budgets.quota import QuotaEnforcer
from telemetry.budgets.killswitch import KillSwitchController
from telemetry.budgets.preflight import preflight, first_blocking_rail

# ... wire enforcers over a SpendLedger (metering feed or static) ...
result = preflight("acme", vendor="anthropic", model="claude-3-5-sonnet",
                   requested_cost_usd=0.5, requested_tokens=5_000,
                   killswitch=ks, quota=quota, budget=budget)
if not result.allowed:
    rail = first_blocking_rail(result)
    # attach rail.outcome (refused/blocked/budget_exceeded) and refuse the call
```

### Budgets

`config/policies.yaml` declares per-tenant policies: a monthly cost limit, a
daily token limit, and optional per-vendor caps (the per-vendor/model
ladder). Each has `warnAtPct` (default 0.8). Enforcement reads **current
spend from the durable metering feed** (issue #33) through the `SpendLedger`
protocol — never a per-process counter, correct across restarts/instances.

| Below `warnAtPct` | From `warnAtPct` on | At/above the limit (100%) |
|---|---|---|
| `allow` | `warn` (flagged) | `block` (refused) |

**Safe rollout:** a policy's `mode` defaults to `observe` — an over-budget
tenant is reported (`would_block`) and never refused. Flip a tenant to
`enforce` only after a recorded track record exists (seeded examples in the
config are `enforce` to demonstrate real blocking). An `enforce` block maps
onto the metering non-billable outcome `budget_exceeded`.

### Quotas (incl. plan overrides — entitlements link, phase 6)

`config/quotas.yaml` declares per-plan defaults (`free`/`standard`/`premium`/
`enterprise`) and per-tenant rows that reference a `plan` and may override
any resource. Effective limits = **plan defaults < per-tenant override**.
Phase-6 entitlements will drive plan membership; this lane ships the
plan-keyed override mechanics as a consumed config contract.

Resources: `requests` (calls/day), `tokens` (day), `concurrency` (in-flight),
`storage` (bytes). Calls/tokens usage is the durable metering feed;
concurrency/storage come from an injected live `StateProbe` (a deployment
wires the gateway in-flight counter / control-plane usage there).

### Global kill switch

`config/killswitch.yaml` ships `globalPause: false` (new controls default
OFF). `KillSwitchController.pause(reason, paused_by)` engages it (audited);
`clear()` restores operations (audited). While engaged,
`check_call(tenant, service=..., critical=...)` refuses every call that is
not critical / on an exempt service — a **normally-allowed call is refused
the instant the switch is ON**. Every refusal is audited; critical/exempt
pass-throughs are audited too (never silent). The `GLOBAL_PAUSE` env var
(`true`/`1`/`yes`) engages the switch at boot (cannibalized source's
incident override).

### Audit

`audit.py` records every BLOCK/WARN decision (and observe-mode `would_*`), a
kill-switch refuse, and pause/clear/exempt transitions to an append-only
JSONL store (`JsonlAuditStore`) — durable across restarts. Plain `allow`s are
not recorded (no signal).

### SLO/budget export + chargeback

`exporter.py` (`BudgetStateExporter`) renders one machine-readable snapshot:
kill-switch state, per-tenant budget positions, per-tenant quota statuses,
the **SLO feed** (issue-#32 `SloResult` objects or their serialized dicts —
verbatim, never re-evaluated) and an audit summary — written as JSON for
dashboards/alerting.

`chargeback.py` (`ChargebackReportGenerator`) emits per-tenant chargeback
lines (calls/tokens/cost per month) as CSV/JSON from the metering rollups —
an honest sum; unmetered calls are surfaced separately, never billed as zero.

## Module layout

| File | Purpose |
|------|---------|
| `model.py` | Decision/resource/window vocabulary + `EnforcerDecision` value object. |
| `ledger.py` | `SpendLedger` protocol + `MeteringReporterLedger` adapter (consumes the issue-#33 feed). |
| `budget.py` | `BudgetEnforcer` — per-tenant/per-vendor warn→block ladder, observe/enforce modes. |
| `quota.py` | `QuotaEnforcer` — soft/hard quotas + plan-default resolution + live probe. |
| `killswitch.py` | `KillSwitchController` — global pause (flag-gated OFF), audited set/clear/refuse. |
| `audit.py` | `BudgetAuditStore` / `JsonlAuditStore` — durable BLOCK/WARN/refusal audit feed. |
| `preflight.py` | `CallPreflight` — composed pre-dispatch check (kill switch → quota → budget). |
| `exporter.py` | `BudgetStateExporter` — machine-readable state + SLO feed for dashboards/alerts. |
| `chargeback.py` | `ChargebackReportGenerator` — per-tenant chargeback lines for billing. |
| `config/` | `policies.yaml`, `quotas.yaml`, `killswitch.yaml` seed configs. |
| `cli.py` | Offline operator CLI (`python3 -m telemetry.budgets.cli …`). |
| `tests/` | pytest suite incl. the fail-closed negatives. |

## Usage

All commands run from the repo root (offline):

```bash
# Dump loaded config
python3 -m telemetry.budgets.cli policies
python3 -m telemetry.budgets.cli quotas

# Kill switch (state persists to the file you pass)
python3 -m telemetry.budgets.cli killswitch status
python3 -m telemetry.budgets.cli killswitch pause --reason "incident #9" --by oncall \
    --state /tmp/ks-state.yaml --audit /tmp/audit.jsonl
python3 -m telemetry.budgets.cli killswitch resume --state /tmp/ks-state.yaml --audit /tmp/audit.jsonl

# Pre-dispatch check (composed; exit 1 = refused)
python3 -m telemetry.budgets.cli check --tenant acme --vendor anthropic \
    --cost 0.5 --tokens 5000 --store /tmp/usage.jsonl --audit /tmp/audit.jsonl

# Single rails
python3 -m telemetry.budgets.cli budget --tenant acme --cost 0.5 --tokens 5000
python3 -m telemetry.budgets.cli quota --tenant acme --resource requests --requested 1

# State export (dashboards/alerts) + chargeback report (billing)
python3 -m telemetry.budgets.cli export --store /tmp/usage.jsonl \
    --audit /tmp/audit.jsonl --out /tmp/budgets-state.json
python3 -m telemetry.budgets.cli chargeback --store /tmp/usage.jsonl \
    --month 2026-09 --out /tmp/chargeback.csv

# End-to-end evidence walk-through
python3 -m telemetry.budgets.cli demo
```

Python API:

```python
from telemetry.budgets.budget import BudgetEnforcer
from telemetry.budgets.ledger import MeteringReporterLedger
from telemetry.metering.report import UsageReporter
from telemetry.metering.store import JsonlUsageStore

ledger = MeteringReporterLedger(UsageReporter(JsonlUsageStore(Path("/tmp/usage.jsonl"))))
enforcer = BudgetEnforcer(ledger)          # loads config/policies.yaml
decision = enforcer.check("acme", vendor="anthropic", model="claude-3-5-sonnet",
                          requested_cost_usd=0.5, requested_tokens=5000)
if decision.decision == "block":
    raise Blocked(decision.outcome)        # budget_exceeded — never meter the call
```

## Verification

```bash
python3 -m pytest telemetry/budgets/tests -q -p no:cacheprovider
python3 -m telemetry.budgets.cli demo
make verify       # repo gate stays green (run from the worktree root)
```

## Provenance (cannibalized sources)

All sources verified present under the fleet `.research/` tree
(`/home/akushnir/agent-orchestrator/.research/`, gitignored):

| Source (repo/path) | What was adapted |
|---|---|
| `shared-services` `cost-governance/budget_enforcer.py` | Per-vendor budget enforcement + alert-threshold ladder (70/90/100%) and hard cutoff (auto-reject) behind the warn→block ladder + hard-cap stop in `budget.py`. |
| `shared-services` `automation/telemetry/global_pause.py` | The global pause/kill-switch flag: check-before-act, owner-triggerable set/clear with reason, hard-stop that blocks even approved actions, env override — behind `killswitch.py`. |
| `shared-services` `services/resource-quota-enforcer/quota_enforcer.py` | Per-tenant soft/hard resource quotas + status ladder (ok/warning/critical/exceeded) over resource types — behind `quota.py` (calls/tokens/concurrency/storage). |
| `shared-services` `services/tenant-slo-exporter/main.py` (README) | The READY-TO-REUSE tenant SLO exporter framing consumed by `exporter.py`'s SLO feed. |
| `leaderboard` `scripts/ops/finops-governor.sh` + `config/finops-budget.yaml` | Budget-governor + YAML budget policy shape behind `config/policies.yaml` + the enforcer. |
| `leaderboard` `scripts/guard/backpressure.sh` *(issue-listed path absent)* | Backpressure concept acknowledged; quota soft/hard refusal mechanics cover the same ground offline. |
| `CMR` `guardrails/policy/controls.yaml` + `fleet/cost-report.sh` | Control/flag gating (default OFF) + cost-report conventions behind the kill-switch default and `chargeback.py`. |
| `monitoring-stack` `scripts/chargeback-report-generator.py` | Monthly per-tenant chargeback from billing/usage data behind `ChargebackReportGenerator` (offline over the metering rollups). |
