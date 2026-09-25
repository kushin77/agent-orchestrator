# Promotion audit record - `surfaces.finops_reports` gradual -> full

One transition, written by the promotion path immediately BEFORE the
live-state entry that names it (`infra/rollout/cli.py promote --live-state-out`,
and the ordered driver `infra/rollout/go_live.py`). `check_rollout.py` refuses a
promoted flag whose record is missing, so this file is what makes the promotion
provable rather than claimed.

| field | value |
|---|---|
| flag | `surfaces.finops_reports` |
| transition | `gradual` -> `full` |
| actor | `deployer-sa` |
| approval | human approval_id `ao-20260925T203034Z-surfaces-finops_reports-full` |
| verify_green | true |
| recorded_at | 2026-09-25T21:50:45Z |
| live-state | `infra/rollout/live-state.yaml` |
| audit log | `infra/rollout/audit/promotion-audit.jsonl` seq 80, hash `ef99489e3c6f7ce9a0a62ebbe10bb403e068e746dae7141a41905d338f73fc72` |

## The command (re-runnable)

```
python3 -m infra.rollout.cli promote surfaces.finops_reports --to full --actor deployer-sa --approval ao-20260925T203034Z-surfaces-finops_reports-full --approvals-dir infra/rollout/approvals --audit-log infra/rollout/audit/promotion-audit.jsonl --live-state-out infra/rollout/live-state.yaml
```

## What the engine reported

```
promoted surfaces.finops_reports gradual -> full (100%)
audit log seq=80 hash=ef99489e3c6f7ce9a0a62ebbe10bb403e068e746dae7141a41905d338f73fc72
```

A record is evidence, not a claim: the audit-log line above is machine-checkable
(`AuditLog.verify` recomputes the sha-256 chain) and the transition it names is
the one the live-state entry carries.
