# Promotion audit record - `surfaces.ops_health` off -> canary

One transition, written by the promotion path immediately BEFORE the
live-state entry that names it (`infra/rollout/cli.py promote --live-state-out`,
and the ordered driver `infra/rollout/go_live.py`). `check_rollout.py` refuses a
promoted flag whose record is missing, so this file is what makes the promotion
provable rather than claimed.

| field | value |
|---|---|
| flag | `surfaces.ops_health` |
| transition | `off` -> `canary` |
| actor | `deployer-sa` |
| approval | policy `low-risk-auto-approve` (auto-approved on green verification evidence) |
| verify_green | true |
| recorded_at | 2026-09-25T21:50:13Z |
| live-state | `infra/rollout/live-state.yaml` |
| audit log | `infra/rollout/audit/promotion-audit.jsonl` seq 70, hash `3c01d585d47eb1aa5f4f934956a7382c1284e08f7cc059dfed64fe0b8bd5ac0f` |

## The command (re-runnable)

```
python3 -m infra.rollout.cli promote surfaces.ops_health --to canary --actor deployer-sa --approval <approval-id> --approvals-dir infra/rollout/approvals --audit-log infra/rollout/audit/promotion-audit.jsonl --live-state-out infra/rollout/live-state.yaml
```

## What the engine reported

```
promoted surfaces.ops_health off -> canary (5%)
audit log seq=70 hash=3c01d585d47eb1aa5f4f934956a7382c1284e08f7cc059dfed64fe0b8bd5ac0f
```

A record is evidence, not a claim: the audit-log line above is machine-checkable
(`AuditLog.verify` recomputes the sha-256 chain) and the transition it names is
the one the live-state entry carries.
