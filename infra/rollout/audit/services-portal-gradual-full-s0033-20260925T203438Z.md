# Promotion audit record - `services.portal` gradual -> full

One transition, written by the promotion path immediately BEFORE the
live-state entry that names it (`infra/rollout/cli.py promote --live-state-out`,
and the ordered driver `infra/rollout/go_live.py`). `check_rollout.py` refuses a
promoted flag whose record is missing, so this file is what makes the promotion
provable rather than claimed.

| field | value |
|---|---|
| flag | `services.portal` |
| transition | `gradual` -> `full` |
| actor | `deployer-sa` |
| approval | human approval_id `ao-20260925T203034Z-services-portal-full` |
| verify_green | true |
| recorded_at | 2026-09-25T20:34:38Z |
| live-state | `live-state.yaml` |
| audit log | `audit/promotion-audit.jsonl` seq 33, hash `e1a5e7b34d6a99bca132ec276f205e28c165866a4901891fa66aa91e218f869b` |

## The command (re-runnable)

```
python3 infra/rollout/go_live.py --phase 0-7 --canary-health-ok --gradual-complete --actor deployer-sa
```

## What the engine reported

```
promoted services.portal gradual -> full
```

A record is evidence, not a claim: the audit-log line above is machine-checkable
(`AuditLog.verify` recomputes the sha-256 chain) and the transition it names is
the one the live-state entry carries.
