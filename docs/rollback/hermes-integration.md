# Rollout + rollback — `hermes-integration`

Path: `integrations/hermes`. The Hermes local-agent-gateway integration.

## Rollout

Ships on `master` merge; `scripts/check-hermes-integration.sh` is the
dedicated gate. `integrations/hermes/policy.py` declares acceptance rules for
what Hermes-originated work is admitted; `integrations/hermes/sync/live.py`
is the live_sync module keeping the integration's view of Hermes state
current.

## Detection

- `bash scripts/check-hermes-integration.sh` — the dedicated gate.
- Work from Hermes being admitted that should have been rejected by
  `policy.py` (or the reverse) is the acceptance-policy regression signal.
- `sync/live.py` stalling shows as the integration not reflecting Hermes-side
  state changes (agent connect/disconnect, tool availability) even though
  Hermes itself is healthy — check the sync process before assuming Hermes
  is down.

## Rollback

1. **Bad acceptance policy**: `git revert <commit>` to `policy.py` on
   `master`; redeploy/restart the process reading it.
2. **Sync stall or protocol drift with Hermes**: restart `sync/live.py`
   first (cheap, no deploy); if the drift is a protocol/schema change on
   either side, revert the integration code change specifically rather than
   anything on the Hermes side (which is out of this repo's scope — this
   repo's rollback is limited to the integration surface, not upstream
   Hermes).

Affected: every agent invocation routed through the Hermes local gateway —
a bad acceptance policy can wrongly admit or block Hermes-originated work
fleet-wide until reverted.
