# Promotion audit record - `surfaces.chat` canary -> gradual

One transition, written by the promotion path immediately BEFORE the
live-state entry that names it (`infra/rollout/cli.py promote --live-state-out`,
and the ordered driver `infra/rollout/go_live.py`). `check_rollout.py` refuses a
promoted flag whose record is missing, so this file is what makes the promotion
provable rather than claimed.

| field | value |
|---|---|
| flag | `surfaces.chat` |
| transition | `canary` -> `gradual` |
| actor | `deployer-sa` |
| approval | policy `low-risk-auto-approve` (auto-approved on green verification evidence) |
| verify_green | true |
| recorded_at | 2026-09-25T21:50:26Z |
| live-state | `infra/rollout/live-state.yaml` |
| audit log | `infra/rollout/audit/promotion-audit.jsonl` seq 76, hash `92fb244b4b42c92d17711185952804c341a371689d6c6ef06d1b7e95f3765011` |

## The command (re-runnable)

```
python3 -m infra.rollout.cli promote surfaces.chat --to gradual --actor deployer-sa --approval <approval-id> --approvals-dir infra/rollout/approvals --audit-log infra/rollout/audit/promotion-audit.jsonl --live-state-out infra/rollout/live-state.yaml
```

## What the engine reported

```
promoted surfaces.chat canary -> gradual (10%)
audit log seq=76 hash=92fb244b4b42c92d17711185952804c341a371689d6c6ef06d1b7e95f3765011
```

A record is evidence, not a claim: the audit-log line above is machine-checkable
(`AuditLog.verify` recomputes the sha-256 chain) and the transition it names is
the one the live-state entry carries.
