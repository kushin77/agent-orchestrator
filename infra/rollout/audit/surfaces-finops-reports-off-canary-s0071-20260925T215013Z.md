# Promotion audit record - `surfaces.finops_reports` off -> canary

One transition, written by the promotion path immediately BEFORE the
live-state entry that names it (`infra/rollout/cli.py promote --live-state-out`,
and the ordered driver `infra/rollout/go_live.py`). `check_rollout.py` refuses a
promoted flag whose record is missing, so this file is what makes the promotion
provable rather than claimed.

| field | value |
|---|---|
| flag | `surfaces.finops_reports` |
| transition | `off` -> `canary` |
| actor | `deployer-sa` |
| approval | policy `low-risk-auto-approve` (auto-approved on green verification evidence) |
| verify_green | true |
| recorded_at | 2026-09-25T21:50:13Z |
| live-state | `infra/rollout/live-state.yaml` |
| audit log | `infra/rollout/audit/promotion-audit.jsonl` seq 71, hash `83a318b6de776ef56a0f5b2433f20ad2ec6525c1dcaa51f7d1c191cc0896a66d` |

## The command (re-runnable)

```
python3 -m infra.rollout.cli promote surfaces.finops_reports --to canary --actor deployer-sa --approval <approval-id> --approvals-dir infra/rollout/approvals --audit-log infra/rollout/audit/promotion-audit.jsonl --live-state-out infra/rollout/live-state.yaml
```

## What the engine reported

```
promoted surfaces.finops_reports off -> canary (5%)
audit log seq=71 hash=83a318b6de776ef56a0f5b2433f20ad2ec6525c1dcaa51f7d1c191cc0896a66d
```

A record is evidence, not a claim: the audit-log line above is machine-checkable
(`AuditLog.verify` recomputes the sha-256 chain) and the transition it names is
the one the live-state entry carries.
