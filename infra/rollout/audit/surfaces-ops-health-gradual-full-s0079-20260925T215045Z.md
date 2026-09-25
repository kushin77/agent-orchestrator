# Promotion audit record - `surfaces.ops_health` gradual -> full

One transition, written by the promotion path immediately BEFORE the
live-state entry that names it (`infra/rollout/cli.py promote --live-state-out`,
and the ordered driver `infra/rollout/go_live.py`). `check_rollout.py` refuses a
promoted flag whose record is missing, so this file is what makes the promotion
provable rather than claimed.

| field | value |
|---|---|
| flag | `surfaces.ops_health` |
| transition | `gradual` -> `full` |
| actor | `deployer-sa` |
| approval | human approval_id `ao-20260925T203034Z-surfaces-ops_health-full` |
| verify_green | true |
| recorded_at | 2026-09-25T21:50:45Z |
| live-state | `infra/rollout/live-state.yaml` |
| audit log | `infra/rollout/audit/promotion-audit.jsonl` seq 79, hash `2a3fed22f123e50a346b23dfefadcecd1ac8932f51e6f81ec0beeec1ef5874bb` |

## The command (re-runnable)

```
python3 -m infra.rollout.cli promote surfaces.ops_health --to full --actor deployer-sa --approval ao-20260925T203034Z-surfaces-ops_health-full --approvals-dir infra/rollout/approvals --audit-log infra/rollout/audit/promotion-audit.jsonl --live-state-out infra/rollout/live-state.yaml
```

## What the engine reported

```
promoted surfaces.ops_health gradual -> full (100%)
audit log seq=79 hash=2a3fed22f123e50a346b23dfefadcecd1ac8932f51e6f81ec0beeec1ef5874bb
```

A record is evidence, not a claim: the audit-log line above is machine-checkable
(`AuditLog.verify` recomputes the sha-256 chain) and the transition it names is
the one the live-state entry carries.
