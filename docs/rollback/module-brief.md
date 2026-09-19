# Rollout + rollback — `module-brief`

Path: `integrations/paperclip/reporting`. Module briefing/reporting surface
under the Paperclip integration.

## Rollout

Reporting code and its `claim-policy.json` / `policy.py` ship on normal
`master` merges. `integrations/paperclip/reporting/sync/live.py` is the
live_sync module and `sync/sync.schema.json` is the shared schema the sync
payloads are validated against — a schema change and the code that
produces/consumes it must land in the same PR, or the sync starts emitting
or rejecting payloads the other side doesn't understand.

## Detection

- Validate live sync payloads against `sync/sync.schema.json` directly — a
  validation failure after a rollout means schema and producer/consumer drifted.
- `claim-policy.json` / `policy.py` — a claim that should be
  accepted/rejected doing the opposite is the acceptance-policy regression
  signal for this surface.
- `sync/live.py` stalling shows as briefs/reports not reflecting recent
  module activity even though the source data changed.

## Rollback

1. **Schema/producer drift**: revert schema and code together
   (`git revert <commit>` on the combined PR) — never revert only one side.
2. **Bad claim policy**: `git revert <commit>` to `claim-policy.json` /
   `policy.py`; pure config/code, redeploy the reporting process.
3. **Sync stall**: restart `sync/live.py`.

Affected: consumers of module briefs/reports (anything reading through
Paperclip's reporting integration) — a schema-drift rollback restores
report generation for every module briefed through this path.
