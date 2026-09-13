# RCA-0004 — the claim audit replayed history against present truth

| Field | Value |
|---|---|
| RCA id | `RCA-0004` |
| Incident | `INC-0004` |
| Origin | `#157` — the claim gate whose replay was wrong |
| Severity | `high` |
| Owner | governance lane |
| Reviewed | `2026-09-13` |

## Impact

The claim gate failed an agent that had **already completed** its work, with the
finding `#139: claimed after it was closed`. The claim record was correct; the
replay that judged it was not. A gate that retroactively condemns completed work
is worse than no gate: it teaches lanes to distrust the ledger and to edit
history to satisfy it.

## Detection

Immediately — the audit failed on a clean checkout while the same commit's
ledger history was, in fact, accurate. Diagnosing it required diffing the
snapshot the replay used against the ledger's own timestamps.

## Root cause

The replay compared a **historical** claim event against **today's** snapshot. A
frontier ("the next eligible issue") is a point-in-time property: it is derived
from the board as it stood when the claim was taken, and it cannot be recomputed
from the present. The rule under audit ("an agent may only claim the next issue
in the active chain") is about the claim instant, so applying it to a released
claim re-judges a decision that is already finished.

## Corrective actions

- `CA-0006` — treat a released claim as finished history: the "claimed after it
  was closed" and milestone-frontier checks now apply only to a claim still live
  at audit time. Landed in PR #172 (`b35fcd0`,
  `governance/dispatch/claims.py`).

## Lessons

`LESSON-0004` — a frontier is a point-in-time property; never recompute a
historical decision from today's snapshot. If a replay disagrees with an
accurate ledger, suspect the replay.

## Evidence

- PR #172 — `b35fcd0`; `git show b35fcd0 -- governance/dispatch/claims.py`
  contains the live-claims guard.
- The original false finding, reproduced before the fix:
  `#139: claimed after it was closed` on the audit of a completed issue.

## Follow-up

Nothing outstanding. The freshness defect that feeds the same replay is the
subject of `RCA-0005`.
