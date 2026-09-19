# Rollout + rollback — `tagging`

Path: `governance/tagging`. Issue/lane tagging policy and live tag state.

## Rollout

Ships on `master` merge; `scripts/check-tagging.sh` is the dedicated gate.
`governance/tagging/controls.yaml` + `policy.py` declare valid tags and
tagging rules; `governance/tagging/live.py` is the live_sync module tracking
live tag assignments.

## Detection

- `bash scripts/check-tagging.sh` — the dedicated gate.
- Issues/lanes receiving tags that violate `controls.yaml` (or failing to
  receive required tags) is the acceptance-policy regression signal — this
  is typically low-severity (routing/reporting impact) rather than a
  correctness incident, unlike `isolation` or `lifecycle`.
- `live.py` stalling shows as tag state lagging actual issue/lane state,
  which can misroute lanes that depend on tags for dispatch/reporting.

## Rollback

1. **Bad tagging policy**: `git revert <commit>` to `controls.yaml` /
   `policy.py` on `master`; re-run `scripts/check-tagging.sh`.
2. **Sync stall**: restart `live.py`.
3. **Mistagged issues/lanes under the bad policy**: since tags feed routing
   (e.g. dispatch reads tags), audit anything dispatched based on a bad tag
   during the window and re-route/re-tag by hand — the code revert prevents
   new mistagging, it does not retroactively fix already-mistagged items.

Affected: any lane/issue routing or reporting that keys off tags — lower
blast radius than `isolation`/`lifecycle`/`dispatch` since tagging is
metadata, not the claim/lease mechanism itself, but a sustained bad rollout
can misroute a large batch of work before detection.
