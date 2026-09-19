# Rollout + rollback — `lifecycle`

Path: `governance/lifecycle`. Lane/issue lifecycle: reclaim and verify-order
enforcement.

## Rollout

Ships on `master` merge; two dedicated gates:
`scripts/check-lifecycle-reclaim.sh` and
`scripts/check-lifecycle-verify-order.sh`. `governance/lifecycle/controls.yaml`
+ `policy.py` declare the lifecycle rules (when a lane can be reclaimed, what
order verification must run in); `governance/lifecycle/live.py` is the
live_sync module tracking live lifecycle state.

## Detection

- Run both dedicated gates directly — `check-lifecycle-reclaim.sh` catches a
  reclaim happening out of policy (too early, on a lane still owned), and
  `check-lifecycle-verify-order.sh` catches verification running out of the
  declared order.
- A lane reclaimed while still legitimately in-flight (work lost) is the
  sharpest real-world signal of a reclaim-policy regression.
- `live.py` stalling shows as the lifecycle state view lagging actual lane
  state, which can trigger false reclaim decisions downstream.

## Rollback

1. **Bad reclaim or verify-order policy**: `git revert <commit>` to
   `controls.yaml` / `policy.py` on `master`; re-run the matching dedicated
   gate to confirm.
2. **A lane wrongly reclaimed under the bad policy**: this is a data
   incident, not just a code one — the reclaimed lane's owner needs to
   re-claim and re-verify from scratch; the code revert prevents recurrence,
   it does not undo the reclaim.
3. **Sync stall**: restart `live.py` before treating a reclaim decision as a
   policy bug.

Affected: every lane going through claim/verify/reclaim — a reclaim-policy
regression can strip an in-flight lane from its owner and hand it to
another, which is a correctness incident independent of the code fix.
