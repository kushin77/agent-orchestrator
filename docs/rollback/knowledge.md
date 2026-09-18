# Rollout + rollback — `knowledge`

Path: `governance/knowledge`. Knowledge index, with secret-handling policy.

## Rollout

Ships on `master` merge; `scripts/check-knowledge-index.sh` is the dedicated
gate. `governance/knowledge/knowledge_controls.py` and `secretpolicy.py`
declare what may be indexed and how secrets are handled (redaction/exclusion)
in indexed content; `governance/knowledge/live_sync.py` is the live_sync
module (its own event schema is
`governance/knowledge/live-sync-event.schema.json`) that keeps the index
current without a full reindex.

## Detection

- `bash scripts/check-knowledge-index.sh` — the dedicated gate.
- Validate `live_sync.py` events against `live-sync-event.schema.json` — a
  validation failure means the sync producer and schema drifted.
- `secretpolicy.py` failing to redact/exclude something it should (or
  over-redacting legitimate content) is the highest-severity regression for
  this surface — a secret-policy rollback is urgent, not routine, because a
  leaked secret in an index cannot be un-indexed by a later revert alone.

## Rollback

1. **Secret-policy regression**: `git revert <commit>` to `secretpolicy.py`
   immediately; if content was already indexed under the bad policy, purge
   and reindex the affected content — the code revert stops new leaks, it
   does not remove what already landed in the index.
2. **Bad indexing/controls change** (`knowledge_controls.py`): revert and
   trigger a reindex if the bad change altered what content was included.
3. **Schema/producer drift**: revert schema and `live_sync.py` together.

Affected: every consumer of the knowledge index; a secret-policy regression
is a security incident, not just a functional one, and its rollback runbook
includes purge-and-reindex, not code revert alone.
