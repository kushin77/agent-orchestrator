# telemetry — Observability (pillar 5, phase 5)

Owner lane: **telemetry**. See [`../docs/EXECUTION-PLAN.md`](../docs/EXECUTION-PLAN.md).

## Purpose

Full-trace observability across the platform: token/latency traces, audit
log, FinOps usage metering for tenant billing (EPIC-00, issue #4).

## Contents

- `ledger/` — durable audit-event ledger (`audit_event.schema.json`).
- `audit/` — audit log of agent actions and policy decisions.
- `observability/` — trace collection (token, latency, per-step spans).
- `metering/` — FinOps usage metering: rate cards, idempotent durable
  ingest/aggregation, daily/monthly rollups, cost attribution (issue #33).
  See `metering/README.md`.
- `budgets/` — per-tenant budget enforcement on top of metering.
- `role_health.py` / `role_health_tests/` — role health checks.

## Live usage feed (issue #886)

`metering/feed.py`'s `LiveUsageFeed` is the one **live** surface of this
pillar: it re-reads the metering ledger (`metering/store.py`'s
`JsonlUsageStore`) on every call — no caching, no snapshot staleness — and
serves the current per-tenant spend/usage rows for the portal to poll. Every
row is validated against `metering/feed.schema.json` before being returned;
a malformed row is refused (`FeedValidationError`), never served (see
`metering/tests/test_feed.py`, including the negative control: a row with a
non-numeric or boolean-masquerading-as-numeric `costUsd` is refused by name).

```python
from telemetry.metering.feed import LiveUsageFeed

feed = LiveUsageFeed.from_path("path/to/metering.jsonl")
for row in feed.rows():
    print(row["tenantId"], row["costUsd"])
```

A consumer for this feed (e.g. a portal FinOps dashboard) is tracked under
issue #665 and is out of scope here — this module only guarantees the feed
itself is real, live, schema-validated, and tested.

## Gate

`scripts/check-telemetry.sh` is this surface's dedicated gate
(`scripts/check-surface-class.sh` `gate` evidence): it validates every
`metering/rate_cards/*.yaml` file parses, that every Anthropic model id
referenced from `gateway/finops/tiers.yaml` and `gateway/health/health.yaml`
resolves (through the shared `metering/model_aliases.py` normalization) to
a priced entry in `metering/rate_cards/anthropic.yaml`, and that the
`metering` pytest suite is green — then runs a negative control (a rate-card
mutant with an entry deleted must be refused by name,
`TELEMETRY-RATECARD-MISSING`). Non-Anthropic gaps referenced from the same
two files (owned by other lanes, out of this lane's file scope) are reported
as a warning rather than silently ignored or turned into a false failure of
this surface's gate.

## Status

Implemented lanes: `ledger`, `audit`, `observability`, `metering`, `budgets`.
Rate cards are point-in-time list prices for cost estimation only — not live
pricing, not customer billing; re-verify before relying on them.
