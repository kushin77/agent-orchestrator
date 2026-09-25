# Promotion audit record - `surfaces.live_bridge` canary -> gradual

One transition, written by the promotion path immediately BEFORE the
live-state entry that names it (`infra/rollout/cli.py promote --live-state-out`,
and the ordered driver `infra/rollout/go_live.py`). `check_rollout.py` refuses a
promoted flag whose record is missing, so this file is what makes the promotion
provable rather than claimed.

| field | value |
|---|---|
| flag | `surfaces.live_bridge` |
| transition | `canary` -> `gradual` |
| actor | `deployer-sa` |
| approval | policy `low-risk-auto-approve` (auto-approved on green verification evidence) |
| verify_green | true |
| recorded_at | 2026-09-25T21:50:12Z |
| live-state | `infra/rollout/live-state.yaml` |
| audit log | `infra/rollout/audit/promotion-audit.jsonl` seq 68, hash `1b6e9148adc507ebf797b9759fa43f6a06862f8b6793905fcb5c7ce847840bc7` |

## The command (re-runnable)

```
python3 -m infra.rollout.cli promote surfaces.live_bridge --to gradual --actor deployer-sa --approval <approval-id> --approvals-dir infra/rollout/approvals --audit-log infra/rollout/audit/promotion-audit.jsonl --live-state-out infra/rollout/live-state.yaml
```

## What the engine reported

```
promoted surfaces.live_bridge canary -> gradual (10%)
audit log seq=68 hash=1b6e9148adc507ebf797b9759fa43f6a06862f8b6793905fcb5c7ce847840bc7
```

A record is evidence, not a claim: the audit-log line above is machine-checkable
(`AuditLog.verify` recomputes the sha-256 chain) and the transition it names is
the one the live-state entry carries.
