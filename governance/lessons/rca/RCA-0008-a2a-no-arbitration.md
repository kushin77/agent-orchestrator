# RCA-0008 — agent-to-agent dispatch with no arbiter

| Field | Value |
|---|---|
| RCA id | `RCA-0008` |
| Incident | `INC-0008` |
| Origin | `event: a2a-no-arbitration-2026-09-14` — the committed board snapshot does not carry #726 (see [`RCA-0012`](RCA-0012-stale-snapshot-no-trigger.md)) |
| Severity | `high` |
| Owner | dispatch lane — issue #726 (EPIC #708) |
| Reviewed | `2026-09-14` |

## Impact

Two agents can be pointed at the same work and **nothing decides between them**.
The fleet has an agent-to-agent transport, and the dispatch path records a claim
per issue, but no rule names who wins when two agents address the same target:
there is no tie-break, no terminal "refused, another agent holds this" verdict
published back to the caller, and no arbiter that the losing agent's work is
attributed to. The observable damage is the class already recorded as
`INC-0003`: two lanes working one issue in parallel and shipping a
**byte-identical 53-line change**. The cost is duplicated tokens and time, and —
worse — a shared file written by two writers with no serialization.

## Detection

After the fact, by a human or a gate comparing outcomes — not by the dispatch
path. Measured on this tree at delivery time:

```text
grep -rc "arbitrat" fleet/channel.py fleet/CONTRACT.md   -> 0, 0
grep -rln "arbitrat" fleet/                              -> no matches
```

The transport exists (`fleet/tests/test_a2a_transport.py`); the arbitration
vocabulary does not appear anywhere in `fleet/`, including its tests. A gap that
leaves no trace in the code leaves no trace in the logs either.

## Root cause

Arbitration was an **assumption, not a mechanism**. Three parts of the picture
each did their own job and none of them decided anything:

- the **channel** is a transport: it moves a message between agents and has no
  decision rule;
- the **claim ledger** is per-issue bookkeeping: it records who claimed what, and
  refuses a duplicate claim, but it is not consulted by the agent-to-agent path
  and does not arbitrate a live conflict;
- the **runtime singleton** (`fleet/singleton.py`) serializes the *process*, not
  the *work*.

With no arbiter, "who owns this" is resolved socially — by whichever agent
stopped first or wrote last. That is the definition of a race, and the
measured 53-line duplicate is what a race looks like in a repository.

## Corrective actions

- `CA-0010` — give the agent-to-agent dispatch path a single arbitration rule:
  one arbiter per target, a refusal (not a warning) for a second claimant, and
  the refusal published back to the caller so the loser can be attributed and
  stopped. Tracked by **#726**. Until it lands, a second claimant is a social
  accident rather than a verdict.

## Lessons

`SUGGEST-0006` — **an agent-to-agent path without an arbiter is a broadcast, not
a protocol.** Any route that lets two agents address the same target must name,
in one place, the rule that decides between them and the verdict the loser
receives; "they will coordinate" is not a rule. Recorded as an open suggestion
until #726 lands.

## Evidence

- `grep -rc "arbitrat" fleet/channel.py fleet/CONTRACT.md` → `0`, `0`;
  `grep -rln "arbitrat" fleet/` → no matches (measured 2026-09-14).
- `fleet/tests/test_a2a_transport.py` exists — the transport is real, the
  arbitration is absent.
- Ledger `INC-0003` — two lanes shipped a byte-identical 53-line change
  (`class: duplicate-work`, the same failure class).
- Ledger: `INC-0008`, `RCA-0008`, `CA-0010`, `SUGGEST-0006`.

## Follow-up

The incident closes with `CA-0010` (#726). The `corrective-action-open` deviation
in the lessons report keeps the open action visible on every gate run.
