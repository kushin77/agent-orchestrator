# Rollout + rollback — `telemetry`

Path: `telemetry`. Observability / FinOps: metering, budgets, the audit
ledger.

## Rollout

Code merges to `master` through the normal PR gate; `scripts/check-telemetry.sh`
is the dedicated pre-merge gate for this surface. `telemetry/metering/feed.py`
is the live_sync module that streams metering events — a rollout that changes
its schema or cadence takes effect the next time the feed process restarts,
not on merge.

## Detection

- `bash scripts/check-telemetry.sh` — the dedicated gate; re-run against the
  live tree.
- `telemetry/budgets/killswitch.py` tripping unexpectedly (spend halted with
  no matching real overspend) is the clearest signal of a bad budget-policy
  rollout — check its logged trip reason before assuming a real budget event.
- `telemetry/budgets/quota.py` denials spiking with no matching usage growth
  points at a quota-config regression, not real quota exhaustion.
- `telemetry/chat/budget_guard.py` errors surface as chat calls being blocked
  that should be allowed — a false positive there is a rollback trigger, a
  true positive is not.
- The ledger's `audit_event.schema.json` — a validation failure against it
  (events rejected or malformed) means a schema change shipped without a
  matching writer/reader update.

## Rollback

1. **Killswitch/quota/budget-policy regression**: `git revert <commit>` the
   policy change under `telemetry/budgets/`, redeploy the metering feed
   process, and confirm `telemetry/budgets/killswitch.py` stops tripping on
   the reverted config.
2. **Ledger schema regression**: revert the schema change together with any
   writer that started emitting the new shape in the same commit (a
   half-reverted schema/writer pair is worse than no revert) — schema and
   writer must move together.
3. **Feed cadence/format regression** (`telemetry/metering/feed.py`): revert
   and restart the feed process; downstream FinOps dashboards read stale data
   until the feed is restarted, so a code revert alone is not sufficient —
   confirm the process actually restarted.

Affected: FinOps dashboards and budget enforcement across every
gateway-routed model call; a killswitch false-trip blocks real spend for
every tenant until reverted, which is the highest-urgency failure mode for
this surface.
