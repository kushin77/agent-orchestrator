# RCA-0005 — a stale snapshot named a closed issue as the frontier (open)

| Field | Value |
|---|---|
| RCA id | `RCA-0005` |
| Incident | `INC-0005` |
| Origin | `#157` — detected while auditing the claim gate |
| Severity | `medium` |
| Owner | governance lane |
| Reviewed | `2026-09-13` |

## Impact

The claim gate reported the closed issue #139 as eligible, because it resolved
the milestone frontier from a board snapshot that was **19 minutes out of date**.
A lane acting on that answer would have claimed finished work. The incident is
open: the freshness defect is still present in the gate.

## Detection

During the claim audit — the same audit that produced `RCA-0004`. The snapshot's
`generated_at` timestamp was compared with the audit time and the issue's close
time; the gap is the whole defect.

## Root cause

The gate reads `.board/snapshot.json` as if it were live. Nothing in the code
compares the snapshot's `generated_at` against the request time, and nothing
refuses an answer derived from a snapshot older than an acceptable age. Stale
data was consumed as truth because the reader had no freshness rule at all.

## Corrective actions

- `CA-0007` — add a snapshot-staleness check to the claim gate and store claims
  conflict-free. Tracked as issue #170 (**open**; this is why the incident is
  still open).

## Lessons

`SUGGEST-0002` — a snapshot's timestamp is part of its meaning. Any consumer of
a point-in-time artifact must state the age it will tolerate and refuse (or
warn) beyond it, rather than answering from whatever file happens to be on disk.
Recorded as an open suggestion until #170 lands.

## Evidence

- Issue #170 — `Harden the claim gate: snapshot staleness check + conflict-free
  claim storage` (open).
- `.board/snapshot.json` carries `generated_at`, so staleness is measurable; the
  gate simply did not read it.

## Follow-up

The incident closes with `CA-0007`. Until #170 lands, the claim gate is
advisory whenever the snapshot is older than the audit window, and the
`corrective-action-open` deviation in the lessons report keeps that visible.
