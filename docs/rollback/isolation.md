# Rollout + rollback — `isolation`

Path: `governance/isolation`. Per-lane isolation/leasing, including the
landed-baseline the fleet's isolation checks compare against.

## Rollout

Ships on `master` merge; `scripts/check-isolation-landed.sh` is the dedicated
gate, comparing the tree against `governance/isolation/landed-baseline.json`.
`governance/isolation/controls.yaml` + `policy.py` declare lease/isolation
rules; `governance/isolation/live.py` is the live_sync module tracking
in-flight leases. Because `landed-baseline.json` is itself a tracked,
mutating artifact (updated as lanes land), a rollout here is unusually
sensitive to base — always diff against a specific commit, not "current
master", when rolling back.

## Detection

- `bash scripts/check-isolation-landed.sh` — the dedicated gate; a failure
  names the specific baseline mismatch.
- Two lanes claiming the same file/lease (the isolation guarantee broken) is
  the sharpest signal of a controls/policy regression — this is the failure
  mode `check-isolation-landed` exists to catch by name.
- `governance/isolation/live.py` stalling shows as the lease view lagging
  actual claims, which can produce false isolation-violation reports.

## Rollback

1. **Bad isolation/lease policy** (`controls.yaml` / `policy.py`):
   `git revert <commit>` on `master`, re-run
   `scripts/check-isolation-landed.sh` to confirm leases are recognized
   correctly again.
2. **Corrupted/regressed `landed-baseline.json`**: revert to the last known
   good committed baseline (`git checkout <good-commit> --
   governance/isolation/landed-baseline.json`, committed as its own revert,
   not hand-edited) rather than editing it live — a hand-edit here is exactly
   the kind of drift the gate exists to catch.
3. **Sync stall**: restart the `live.py` process before treating a lease
   conflict as a policy bug.

Affected: every concurrently-running lane/agent in the fleet — an isolation
regression risks two lanes touching the same file, which is the exact
failure this surface is the control for; roll back promptly rather than
patch-forward under load.
