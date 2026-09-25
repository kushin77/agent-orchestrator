# Promotion audit record - `surfaces.live_bridge` off -> canary

One transition, written by the promotion path immediately BEFORE the
live-state entry that names it (`infra/rollout/cli.py promote --live-state-out`,
and the ordered driver `infra/rollout/go_live.py`). `check_rollout.py` refuses a
promoted flag whose record is missing, so this file is what makes the promotion
provable rather than claimed.

| field | value |
|---|---|
| flag | `surfaces.live_bridge` |
| transition | `off` -> `canary` |
| actor | `deployer-sa` |
| approval | policy `low-risk-auto-approve` (auto-approved on green verification evidence) |
| verify_green | true |
| recorded_at | 2026-09-25T21:50:01Z |
| live-state | `infra/rollout/live-state.yaml` |
| audit log | `infra/rollout/audit/promotion-audit.jsonl` seq 67, hash `cbf0dbb25d6481c1e120f47d401a8b076735593dab999c3d919ddd717f85715b` |

## The command (re-runnable)

```
python3 -m infra.rollout.cli promote surfaces.live_bridge --to canary --actor deployer-sa --approval <approval-id> --approvals-dir infra/rollout/approvals --audit-log infra/rollout/audit/promotion-audit.jsonl --live-state-out infra/rollout/live-state.yaml
```

## What the engine reported

```
promoted surfaces.live_bridge off -> canary (5%)
audit log seq=67 hash=cbf0dbb25d6481c1e120f47d401a8b076735593dab999c3d919ddd717f85715b
```

A record is evidence, not a claim: the audit-log line above is machine-checkable
(`AuditLog.verify` recomputes the sha-256 chain) and the transition it names is
the one the live-state entry carries.
