# Heartbeat adapter

Derives the frozen upstream heartbeat (`wake.cause`, `wake.delta`, `outcome`,
`tick`, `cadence_seconds`) from the fleet's real rung activity, closing
[`docs/PAPERCLIP-ING-INTEGRATION.md`](../../../docs/PAPERCLIP-ING-INTEGRATION.md)
§5 mismatches **#1–#3** (heartbeat granularity, state vocabulary, implicit
cadence).

The fleet's beat (`fleet/monitor.py`, `fleet/brain.py`, `fleet/terminal.py`
`write_heartbeat`) is a flat liveness record — `{pid, state, started_at,
commit, ts}` — with no wake, no delta and no outcome. This adapter **derives**
the missing fields; it never rewrites the fleet's beat (ADR-0012: map the
policy, do not couple the runtime).

Contract: [`docs/contracts/paperclip/heartbeat.schema.json`](../../../docs/contracts/paperclip/heartbeat.schema.json).

## Sources (read-only)

| Source | Contributes |
|---|---|
| `.fleet/<rung>.heartbeat.json` | the rung's liveness `state`, `ts`, `commit`, and (sister) `issue`/`agent` |
| `.board/claims.jsonl` + `.board/claims/` | claim/release/reap events → `claimed` / `released` deltas, the `assigned` wake |
| `.board/snapshot.json` | `blocked_by` edges and `closed_at` stamps → `blocked` / `unblocked` / `closed` |
| `.fleet/inbox/`, `.fleet/brain/inbox/` | directives, results and acks → `assigned` / `review-requested` / `commented` |
| `fleet/monitor.py` | `POLL_SECONDS` → `cadence_seconds` (read, never re-declared) |
| `governance/policy/lease.py` | `RUNG_HEARTBEAT_SECONDS` → the staleness ceiling (read, never re-declared) |

## `wake.cause` mapping (deterministic, first match wins)

| Precedence | `wake.cause` | Real source |
|---|---|---|
| 1 | `unblocked` | a blocker of the agent's issue closed since the last beat (`unblocked` delta non-empty) |
| 2 | `review-requested` | a `result` message landed in the rung's mailbox since the last beat |
| 3 | `assigned` | a new claim since the last beat, **or** a `directive` landed in the mailbox |
| 4 | `commented` | any other inbound message (ack / escalate / halt) since the last beat |
| 5 | `scheduled` | a cadence tick with no attributable event |

A rung whose beat carries a `state` outside the known liveness vocabulary
(`healthy`, `idle`, `dispatching`, `working`, `paused`, `stopping`, `stopped`)
is **unattributable** and fails closed, naming the offending state. A rung with
*no beat at all* is provably down: it gets a `scheduled` tick whose `outcome` is
a named blocker rather than a fabricated event cause.

## `wake.delta`

The change since the last beat — always carries `since`, `tick` and `state`
(the rung's transition); the change lists appear only when non-empty:

```json
{"since": "2026-09-14T00:00:20Z", "tick": {"from": 3, "to": 4},
 "state": {"from": "dispatching", "to": "working"},
 "claimed": [414], "unblocked": [400]}
```

A beat that carries **no delta** is refused by name.

## `outcome`

`progress` for durable forward motion (a claimed/closed/unblocked/released delta,
or a live rung on a tick); `blocked` with a named `owner` for a down rung or an
open dependency. A `blocked` outcome with no owner is refused by name.

## Cadence

`cadence_seconds` is read from `fleet/monitor.py` `POLL_SECONDS`; `tick` is the
actual emit count (`len(history)`); the staleness ceiling is read from
`governance/policy/lease.py` `RUNG_HEARTBEAT_SECONDS`. The adapter also asserts
the ordering invariant `cadence < ceiling`, so the implicit cadence is made
explicit without inventing a second scheduler.

## Usage

```bash
python3 -m paperclip.adapters.heartbeat.cli policy
python3 -m paperclip.adapters.heartbeat.cli derive --rung sister --session "$AO_SESSION_ID"
python3 -m paperclip.adapters.heartbeat.cli validate
bash scripts/check-paperclip-heartbeat.sh
```

## Gate

[`scripts/check-paperclip-heartbeat.sh`](../../../scripts/check-paperclip-heartbeat.sh)
proves the contract can fail: it derives a real beat and its negative controls
(a beat with no delta, an unknown `wake.cause`, a `blocked` outcome with no
owner) must each be **refused by name**. The pytest suite lives in
`tests/`.
