# Promotion audit record - `surfaces.telemetry_live_feed` canary -> gradual

One transition, written by the promotion path immediately BEFORE the
live-state entry that names it (`infra/rollout/cli.py promote --live-state-out`,
and the ordered driver `infra/rollout/go_live.py`). `check_rollout.py` refuses a
promoted flag whose record is missing, so this file is what makes the promotion
provable rather than claimed.

| field | value |
|---|---|
| flag | `surfaces.telemetry_live_feed` |
| transition | `canary` -> `gradual` |
| actor | `deployer-sa` |
| approval | policy `low-risk-auto-approve` (auto-approved on green verification evidence) |
| verify_green | true |
| recorded_at | 2026-09-25T21:50:26Z |
| live-state | `infra/rollout/live-state.yaml` |
| audit log | `infra/rollout/audit/promotion-audit.jsonl` seq 73, hash `7f3a326a9d19b74ab95051e75dae30ada3b6569cdf601f7103d8578922f492af` |

## The command (re-runnable)

```
python3 -m infra.rollout.cli promote surfaces.telemetry_live_feed --to gradual --actor deployer-sa --approval <approval-id> --approvals-dir infra/rollout/approvals --audit-log infra/rollout/audit/promotion-audit.jsonl --live-state-out infra/rollout/live-state.yaml
```

## What the engine reported

```
promoted surfaces.telemetry_live_feed canary -> gradual (10%)
audit log seq=73 hash=7f3a326a9d19b74ab95051e75dae30ada3b6569cdf601f7103d8578922f492af
```

A record is evidence, not a claim: the audit-log line above is machine-checkable
(`AuditLog.verify` recomputes the sha-256 chain) and the transition it names is
the one the live-state entry carries.
