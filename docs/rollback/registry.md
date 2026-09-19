# Rollout + rollback — `registry`

Path: `registry`. Agent registry: profiles, packs, prompt modules, drift
control.

## Rollout

Merges to `master` land registry code and data (profiles, packs, prompt
modules) together; `scripts/check-registry-parity.sh` is the dedicated gate
enforcing that `registry/parity/parity.py`'s drift control has no open drift
before merge. `registry/sync/live.py` is the live_sync module — a registry
content change (a new pack, a profile edit) is picked up by anything reading
through the live sync path without a restart; anything reading a cached
snapshot needs one.

## Detection

- `bash scripts/check-registry-parity.sh` — the dedicated gate; a parity
  failure here means the registry and its declared source of truth (the
  catalog it mirrors) have drifted.
- `registry/parity/parity.py` run directly gives the drift detail (which
  pack/profile/module is out of parity), not just pass/fail.
- The append-only event log + pack attestation (the `audit` evidence for this
  surface) — a pack whose attestation fails verification at install/upgrade
  time is a signal a bad pack shipped, independent of parity.
- `registry/sync/live.py` stalling shows as consumers reading a stale
  registry view even though `master` has the fix — check the sync process is
  actually running before assuming the fix didn't land.

## Rollback

1. **Bad pack or profile content**: packs are versioned and attestation-backed
   (`registry/packs/`) — roll back to the prior attested version rather than
   hand-editing; `git revert <commit>` on the registry change, then re-run
   `scripts/check-registry-parity.sh` to confirm parity is restored against
   the reverted state.
2. **Drift-control regression** (`registry/parity/parity.py` itself changed
   and started reporting false drift, or missing real drift): revert the
   parity-control commit specifically — do not revert unrelated pack changes
   bundled in the same PR; they should ship independently going forward.
3. **Sync stall**: restart the `registry/sync/live.py` process; this is an
   operational restart, not a code rollback, and should be tried before
   assuming the shipped fix is bad.

Affected: every consumer that resolves an agent profile, pack, or prompt
module through the registry — a bad pack rollback is scoped to the agents
that reference that pack; a parity-control regression can mask drift across
the whole registry until reverted.
