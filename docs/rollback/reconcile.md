# Rollout + rollback — `reconcile`

Path: `governance/reconcile`. Fleet state reconciliation.

## Rollout

Ships on `master` merge; `scripts/check-reconcile.sh` is the dedicated gate.
`governance/reconcile/controls.yaml` + `policy.py` declare what counts as a
reconcilable discrepancy and how it is resolved; `governance/reconcile/
live.py` is the live_sync module feeding it the live fleet state to reconcile
against.

## Detection

- `bash scripts/check-reconcile.sh` — the dedicated gate; a failure names the
  specific reconciliation rule that broke.
- Reconcile silently "fixing" state that was actually correct (a false
  positive) is as dangerous as missing a real discrepancy (a false negative)
  — check the reconcile audit trail for unexpected corrective writes after a
  rollout, not just gate pass/fail.
- `live.py` stalling shows as reconcile acting on stale fleet state, which
  can produce corrections based on data that is no longer true.

## Rollback

1. **Bad reconcile policy** (`controls.yaml` / `policy.py`): `git revert
   <commit>` on `master`; re-run `scripts/check-reconcile.sh`.
2. **State corrupted by a bad reconcile run**: reconciliation writes are
   corrective actions on live fleet state, not just code — after reverting
   the policy, audit what the bad policy corrected and restore any state it
   wrongly touched from the append-only trail the surface's `audit` evidence
   maintains, rather than assuming the code revert alone undoes prior writes.
3. **Sync stall**: restart `live.py`.

Affected: fleet-wide state consistency — a bad reconcile rollout can
actively corrupt state it was trying to fix, so rollback here includes a
state-repair step, not only a code revert.
