# Rollout + rollback — `dispatch`

Path: `governance/dispatch`. Work-dispatch: entrypoint, queue, reconcile hook.

## Rollout

Ships on `master` merge; three dedicated gates guard it pre-merge:
`scripts/check-dispatch-entrypoint.sh`, `check-dispatch-queue.sh`,
`check-dispatch-reconcile.sh`. `governance/dispatch/controls.yaml` +
`policy.py` declare dispatch acceptance/claim rules; `governance/dispatch/
live.py` / `liveness.py` are the live_sync + liveness modules a running
dispatcher process picks up without necessarily needing a restart, depending
on whether the change is data (queue-visible) or code (process-level).

## Detection

- Run the three dedicated gates directly against the live tree — a failure
  pinpoints entrypoint, queue, or reconcile-hook regression specifically.
- `governance/dispatch/liveness.py` — a dispatcher reporting live while not
  actually processing claims is the liveness-vs-actual-progress gap this
  module exists to catch; watch queue depth growing while liveness reports OK.
- `controls.yaml` / `policy.py` — a claim accepted that should have been
  refused (or the reverse) is the policy-regression signal.

## Rollback

1. **Entrypoint/queue/reconcile regression**: `git revert <commit>` the
   specific change on `master`; re-run the matching dedicated gate
   (`check-dispatch-entrypoint.sh` / `check-dispatch-queue.sh` /
   `check-dispatch-reconcile.sh`) to confirm the revert restores the gate to
   green before declaring rollback complete.
2. **Bad claim policy**: revert `controls.yaml` / `policy.py`; restart the
   dispatcher process since policy is typically read at start.
3. **Stuck queue from a bad rollout** (not a code bug, a poisoned item):
   drain/requeue the specific claim rather than reverting code — check
   `liveness.py`'s reporting to confirm the dispatcher resumes progress after
   the drain.

Affected: every lane/agent whose work is claimed through dispatch — a queue
regression can stall all in-flight claims fleet-wide until reverted or the
poisoned item is drained.
