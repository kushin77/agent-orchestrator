# Promotion audit record - `surfaces.chat` gradual -> full

One transition, written by the promotion path immediately BEFORE the
live-state entry that names it (`infra/rollout/cli.py promote --live-state-out`,
and the ordered driver `infra/rollout/go_live.py`). `check_rollout.py` refuses a
promoted flag whose record is missing, so this file is what makes the promotion
provable rather than claimed.

| field | value |
|---|---|
| flag | `surfaces.chat` |
| transition | `gradual` -> `full` |
| actor | `deployer-sa` |
| approval | human approval_id `ao-20260925T203034Z-surfaces-chat-full` |
| verify_green | true |
| recorded_at | 2026-09-25T21:50:45Z |
| live-state | `infra/rollout/live-state.yaml` |
| audit log | `infra/rollout/audit/promotion-audit.jsonl` seq 81, hash `2984bd4754643520ac975ac4a6f081c4459981ce42a8355bb132ed6dc5acde7c` |

## The command (re-runnable)

```
python3 -m infra.rollout.cli promote surfaces.chat --to full --actor deployer-sa --approval ao-20260925T203034Z-surfaces-chat-full --approvals-dir infra/rollout/approvals --audit-log infra/rollout/audit/promotion-audit.jsonl --live-state-out infra/rollout/live-state.yaml
```

## What the engine reported

```
promoted surfaces.chat gradual -> full (100%)
audit log seq=81 hash=2984bd4754643520ac975ac4a6f081c4459981ce42a8355bb132ed6dc5acde7c
```

A record is evidence, not a claim: the audit-log line above is machine-checkable
(`AuditLog.verify` recomputes the sha-256 chain) and the transition it names is
the one the live-state entry carries.
