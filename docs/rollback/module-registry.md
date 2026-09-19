# Rollout + rollback — `module-registry`

Path: `governance/modules`. The ecosystem module registry (issue #445): one
honest view of every module, assembled by reference from the hub catalog.

## Rollout

`governance/modules/registry.py` assembles its view by reading the hub
catalog plus each module's own `module.json` and the acceptance policy
(`governance/modules/controls.yaml` + `governance/modules/policy.py`); a
rollout here is either a code change to the assembly logic (normal `master`
merge + `make verify`) or a data change (a module's `module.json` declaring a
new `solution_class`), which `governance/conformance/surfaces.py`'s module
floor check enforces cannot exceed the measured floor of its product rows.
`governance/modules/sync/live.py` is the live_sync module keeping the
assembled view current without a redeploy.

## Detection

- Run `governance/modules/registry.py` (or its check target) directly and
  diff the assembled view against the hub catalog — a mismatch means the
  reference assembly is stale or broken.
- `governance/modules/controls.yaml` / `policy.py` — a module that should be
  rejected by the acceptance policy but is accepted (or vice versa) is the
  clearest sign the policy rollout regressed.
- `governance/modules/sync/live.py` stalling shows as the assembled view not
  reflecting a module change that landed on `master`.
- `governance/conformance/surfaces.py check` — a module declaring
  `solution_class` above the measured floor is refused by name
  (`test_mutant_module_declaring_elite_is_refused_by_name`); an unexpected
  pass there means the floor check itself regressed.

## Rollback

1. **Bad acceptance-policy change**: `git revert <commit>` to
   `governance/modules/controls.yaml` / `policy.py` on `master`; this is
   pure code/config, no external flag, so revert-and-redeploy is sufficient.
2. **Bad assembly logic** (`registry.py` producing a wrong view): revert the
   commit; the view is derived, not stored, so reverting the code
   immediately restores the correct assembled view on next read/sync.
3. **Sync stall**: restart the `sync/live.py` process before assuming the
   revert didn't take effect.

Affected: every module author and consumer relying on the registry as the
"one honest view" — a bad acceptance-policy rollout can wrongly admit or
reject modules ecosystem-wide until reverted.
