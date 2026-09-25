# Promotion audit record - `surfaces.live_bridge` gradual -> full

One transition, written by the promotion path immediately BEFORE the
live-state entry that names it (`infra/rollout/cli.py promote --live-state-out`,
and the ordered driver `infra/rollout/go_live.py`). `check_rollout.py` refuses a
promoted flag whose record is missing, so this file is what makes the promotion
provable rather than claimed.

| field | value |
|---|---|
| flag | `surfaces.live_bridge` |
| transition | `gradual` -> `full` |
| actor | `deployer-sa` |
| approval | human approval_id `ao-20260925T203034Z-surfaces-live_bridge-full` |
| verify_green | true |
| recorded_at | 2026-09-25T21:50:44Z |
| live-state | `infra/rollout/live-state.yaml` |
| audit log | `infra/rollout/audit/promotion-audit.jsonl` seq 77, hash `2fd0db316bcb553a217054a59509809a6cdc468ee69f3a3fb26f521a2bfadffc` |

## The command (re-runnable)

```
python3 -m infra.rollout.cli promote surfaces.live_bridge --to full --actor deployer-sa --approval ao-20260925T203034Z-surfaces-live_bridge-full --approvals-dir infra/rollout/approvals --audit-log infra/rollout/audit/promotion-audit.jsonl --live-state-out infra/rollout/live-state.yaml
```

## What the engine reported

```
promoted surfaces.live_bridge gradual -> full (100%)
audit log seq=77 hash=2fd0db316bcb553a217054a59509809a6cdc468ee69f3a3fb26f521a2bfadffc
```

A record is evidence, not a claim: the audit-log line above is machine-checkable
(`AuditLog.verify` recomputes the sha-256 chain) and the transition it names is
the one the live-state entry carries.
