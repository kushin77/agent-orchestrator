# Promotion audit record - `services.telemetry` off -> canary

One transition, written by the promotion path immediately BEFORE the
live-state entry that names it (`infra/rollout/cli.py promote --live-state-out`,
and the ordered driver `infra/rollout/go_live.py`). `check_rollout.py` refuses a
promoted flag whose record is missing, so this file is what makes the promotion
provable rather than claimed.

| field | value |
|---|---|
| flag | `services.telemetry` |
| transition | `off` -> `canary` |
| actor | `deployer-sa` |
| approval | policy `low-risk-auto-approve` (auto-approved on green verification evidence) |
| verify_green | true |
| recorded_at | 2026-09-25T20:34:38Z |
| live-state | `live-state.yaml` |
| audit log | `audit/promotion-audit.jsonl` seq 25, hash `283de67005d2daaec0766f1e08bff2d3beb0d803f803dde338292cbf75ad4ee0` |

## The command (re-runnable)

```
python3 infra/rollout/go_live.py --phase 0-7 --canary-health-ok --gradual-complete --actor deployer-sa
```

## What the engine reported

```
promoted services.telemetry off -> canary
```

A record is evidence, not a claim: the audit-log line above is machine-checkable
(`AuditLog.verify` recomputes the sha-256 chain) and the transition it names is
the one the live-state entry carries.
