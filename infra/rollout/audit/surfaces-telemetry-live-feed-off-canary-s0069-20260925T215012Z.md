# Promotion audit record - `surfaces.telemetry_live_feed` off -> canary

One transition, written by the promotion path immediately BEFORE the
live-state entry that names it (`infra/rollout/cli.py promote --live-state-out`,
and the ordered driver `infra/rollout/go_live.py`). `check_rollout.py` refuses a
promoted flag whose record is missing, so this file is what makes the promotion
provable rather than claimed.

| field | value |
|---|---|
| flag | `surfaces.telemetry_live_feed` |
| transition | `off` -> `canary` |
| actor | `deployer-sa` |
| approval | policy `low-risk-auto-approve` (auto-approved on green verification evidence) |
| verify_green | true |
| recorded_at | 2026-09-25T21:50:12Z |
| live-state | `infra/rollout/live-state.yaml` |
| audit log | `infra/rollout/audit/promotion-audit.jsonl` seq 69, hash `305f0d599839ce904ae7985efcf7b909e133d3db5e0ff5aee64d0cda73df8f4f` |

## The command (re-runnable)

```
python3 -m infra.rollout.cli promote surfaces.telemetry_live_feed --to canary --actor deployer-sa --approval <approval-id> --approvals-dir infra/rollout/approvals --audit-log infra/rollout/audit/promotion-audit.jsonl --live-state-out infra/rollout/live-state.yaml
```

## What the engine reported

```
promoted surfaces.telemetry_live_feed off -> canary (5%)
audit log seq=69 hash=305f0d599839ce904ae7985efcf7b909e133d3db5e0ff5aee64d0cda73df8f4f
```

A record is evidence, not a claim: the audit-log line above is machine-checkable
(`AuditLog.verify` recomputes the sha-256 chain) and the transition it names is
the one the live-state entry carries.
