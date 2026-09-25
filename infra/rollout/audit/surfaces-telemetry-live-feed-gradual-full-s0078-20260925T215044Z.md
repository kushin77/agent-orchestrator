# Promotion audit record - `surfaces.telemetry_live_feed` gradual -> full

One transition, written by the promotion path immediately BEFORE the
live-state entry that names it (`infra/rollout/cli.py promote --live-state-out`,
and the ordered driver `infra/rollout/go_live.py`). `check_rollout.py` refuses a
promoted flag whose record is missing, so this file is what makes the promotion
provable rather than claimed.

| field | value |
|---|---|
| flag | `surfaces.telemetry_live_feed` |
| transition | `gradual` -> `full` |
| actor | `deployer-sa` |
| approval | human approval_id `ao-20260925T203034Z-surfaces-telemetry_live_feed-full` |
| verify_green | true |
| recorded_at | 2026-09-25T21:50:44Z |
| live-state | `infra/rollout/live-state.yaml` |
| audit log | `infra/rollout/audit/promotion-audit.jsonl` seq 78, hash `7e1acc9719eb9d7a491925ae657431e3e5c2a368ec4355f7c3b2cbfc4fc7a998` |

## The command (re-runnable)

```
python3 -m infra.rollout.cli promote surfaces.telemetry_live_feed --to full --actor deployer-sa --approval ao-20260925T203034Z-surfaces-telemetry_live_feed-full --approvals-dir infra/rollout/approvals --audit-log infra/rollout/audit/promotion-audit.jsonl --live-state-out infra/rollout/live-state.yaml
```

## What the engine reported

```
promoted surfaces.telemetry_live_feed gradual -> full (100%)
audit log seq=78 hash=7e1acc9719eb9d7a491925ae657431e3e5c2a368ec4355f7c3b2cbfc4fc7a998
```

A record is evidence, not a claim: the audit-log line above is machine-checkable
(`AuditLog.verify` recomputes the sha-256 chain) and the transition it names is
the one the live-state entry carries.
