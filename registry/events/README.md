# registry/events — append-only agent lifecycle event log

> Owner lane: **registry** (issue #10, work item 06, phase 1). Parent: EPIC-00
> (issue #4). Doctrine: [`AGENTS.md`](../../AGENTS.md),
> [`docs/EXECUTION-PLAN.md`](../../docs/EXECUTION-PLAN.md),
> [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md). Pattern source:
> the CMR `registry/events` append-only log
> ([`docs/CANNIBALIZATION.md`](../../docs/CANNIBALIZATION.md)).

This tree is the **audit trail** of the Agent Identity + Registry service
([`../service/README.md`](../service/README.md), issue #10): a strictly
append-only, hash-chained event log that records every agent lifecycle
transition (`register` / `activate` / `pause` / `retire`), every task-route
change and every identity issuance. It turns "who did what, when, in which
tenant" into a mechanically verifiable record.

## The event contract

One JSON object per line, serialized to a JSON Lines file (or held in memory).
The authoritative shape is [`event.schema.json`](event.schema.json). The closed
core is `seq`, `ts`, `event`, `status`, `tenantId`; the rest is optional per
kind.

| Field | Type | Required | Meaning |
|---|---|---|---|
| `seq` | integer ≥ 1 | ✅ | Monotonic sequence; 1, 2, 3, … with no gaps or duplicates. |
| `ts` | string (RFC 3339 UTC) | ✅ | When the event happened, e.g. `2026-09-08T12:00:00Z`. |
| `event` | `register` \| `activate` \| `pause` \| `retire` \| `route` \| `session` | ✅ | Lifecycle kind (closed enum). |
| `status` | string | ✅ | Outcome/state — see the status vocabulary below. |
| `tenantId` | string | ✅ | The tenant (agent org) the event concerns — always tenant-scoped. |
| `agentId` | string \| null | — | Agent the event concerns; null for `route`. |
| `actor` | string \| null | — | Optional principal that caused the event. |
| `detail` | object \| null | — | Per-kind payload (e.g. `taskType`, `agentIds` for `route`). |
| `prevHash` | string (sha256 hex) | ✅ | Hash of the previous record (fixed genesis hash for `seq` 1). |
| `hash` | string (sha256 hex) | ✅ | sha256 over the record's canonical JSON, `hash` field excluded. |

Example record:

```json
{"seq":1,"ts":"2026-09-08T12:00:00Z","event":"register","status":"registered","tenantId":"acme","agentId":"worker-1","actor":"admin","detail":null,"prevHash":"0000000000000000000000000000000000000000000000000000000000000000","hash":"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"}
```

### Status vocabulary

Conventional `status` per `event` kind (the schema constrains structure; this
table constrains meaning):

| event | conventional `status` |
|---|---|
| `register` | `registered` |
| `activate` | `active` |
| `pause` | `paused` |
| `retire` | `retired` |
| `route` | `set` |
| `session` | `issued` |

## Append-only + integrity

- **Append-only.** Records are never rewritten, deleted or reordered; a new
  record always chains to the previous one. The in-memory `EventLog` and the
  file-backed log (see below) enforce this by construction.
- **Hash chain.** `hash = sha256(canonical JSON of every field except hash)` and
  `prevHash` of record *n* equals `hash` of record *n−1* (the first record's
  `prevHash` is the fixed all-zero genesis hash). Any edit, deletion or
  reordering of a past record breaks `verify`.
- **Truncation.** Within an append-only log, silently dropping *trailing*
  records cannot be detected from the file alone; capture
  `log.state()` → `(last_seq, last_hash)` and pass it to `log.verify(expected=…)`
  (or reopen against that state) to detect truncation.
- **A malformed line is a hard failure.** `EventLog(path=…)` refuses a file
  whose lines do not parse or whose chain does not verify
  (`EventLogIntegrityError`). A gate that cannot fail is a formality.

## File-backed usage

The log can be backed by a JSON Lines file. Every `append` is written in append
mode; reopening the file loads and chain-verifies it.

```bash
# Registry emits to the log automatically (see ../service/README.md)
python3 - <<'PY'
import sys
sys.path.insert(0, "registry")
from events import open_event_log, EventLogIntegrityError

log = open_event_log("registry/events/sample.log")   # creates file if absent
log.append("register", status="registered", tenant_id="acme", agent_id="worker-1")
log.append("activate", status="active", tenant_id="acme", agent_id="worker-1")
print("tail state:", log.state())                    # (2, <last hash>)

# Reopen later; a tampered file raises EventLogIntegrityError on load/verify.
reopened = open_event_log("registry/events/sample.log")
reopened.verify(expected=log.state())
PY
```

## Layout

| Path | Purpose |
|---|---|
| [`event_log.py`](event_log.py) | `EventLog` / `open_event_log` / `now_utc`; hash-chain append-only log. |
| [`event.schema.json`](event.schema.json) | JSON Schema for one event record (the contract). |
| [`__init__.py`](__init__.py) | Public API re-exports. |
| [`tests/`](tests/) | pytest suite (append-only integrity, tamper detection, truncation). |

## Importing and running the tests

`registry/events` is a self-contained package, importable as `events` when
`registry/` is on `sys.path` (the tests arrange this in `tests/conftest.py`):

```bash
# from the repo root
python3 -m pytest registry/events/tests -q -p no:cacheprovider
make verify   # repo gate must stay green (JSON parses; docs/whitespace/markers)
```

## Verification summary (issue #10)

- `python3 -m pytest registry/service/tests registry/events/tests -q` → green
  (see the PR evidence): monotonic sequence, hash-chain verify, tamper
  detection (edit / delete / reorder / truncate), schema conformance of every
  emitted record, and the registry emitting a verifiable lifecycle trail.
- `make verify` → green.

## Provenance (cannibalized and adapted)

Adapted from the sources indexed in
[`docs/CANNIBALIZATION.md`](../../docs/CANNIBALIZATION.md); read through the
`.research/` read-only mirrors (GR-10), not copied verbatim.

| Source | Pattern adapted | Where it landed |
|---|---|---|
| `CMR` `registry/events/` (events.schema.json + JSON Lines log + header comments) | Append-only one-object-per-line event log with a closed event enum | The JSON Lines record format, header comments, closed `event` enum |
| `kushin77/hermes-agents` capability registry | Lifecycle/health vocabulary | `register`/`activate`/`pause`/`retire` kinds |
| `shared-governance` agent-identity standard (action records are append-only events) | Every autonomous action leaves an auditable trace | Lifecycle + route + session audit kinds |
| Fleet no-false-green doctrine (`AGENTS.md`) | A check that cannot fail is a formality | Malformed line / broken chain ⇒ hard `EventLogIntegrityError` |
