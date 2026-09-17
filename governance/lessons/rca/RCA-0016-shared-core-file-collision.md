# RCA-0016 — the shared-core-file collision class

| Field | Value |
|---|---|
| RCA id | `RCA-0016` |
| Incident | `INC-0016` |
| Origin | `#1036` — the PR that re-evidenced the class with a hand-merge conflict |
| Severity | medium |
| Owner | governance / execution-plan lane |
| Reviewed | 2026-09-17 |

This promotes the second of the two lessons from
[`docs/rca/2026-09-16-pr-queue-clearing.md`](../../../docs/rca/2026-09-16-pr-queue-clearing.md)
into the canonical ledger (issue #1052), and records that the class recurred:
PR #1036 conflicted on `governance/lessons/ledger.jsonl` and
`governance/lessons/README.md` — both sides appended ledger rows AND both
hand-edited the same prose incident count, exactly the "two lanes extend the
same core file in parallel, discovered at merge time" shape this RCA
describes.

## Impact

Three PRs collided in the same queue-clearing pass because independent lanes
extended the same "core" file without knowing about each other, and landed
out of order relative to when they were authored: `fleet/watchdog.py` (two
logically-complementary alarm/remedy additions), `fleet/cron.py` (two
independent new-rung additions), and `docs/FLEET-PARITY.md` (created
independently by two lanes). PR #1036 later reproduced the same class against
`governance/lessons/ledger.jsonl` and its own README, requiring a hand-merged
union of two independently-authored prose counts ("Thirteen"/"Eight" →
"Fourteen").

## Detection

Both collisions were discovered at merge time — a conflict marker or a
byte-identical/overlapping diff — not before either lane started.

## Root cause

`docs/EXECUTION-PLAN.md`'s "one issue = one lane = one file set, no two lanes
share a file" doctrine had an explicit exception for genuinely shared,
single-writer build files, but no rule for files that are legitimate
extension points for *more than one feature at once* (`fleet/watchdog.py`,
`fleet/cron.py`, and — as #1036 showed — an append-only ledger plus a
prose-derived count sitting next to it in the same README). Unlike a
single-writer file, nobody should own these permanently: they are supposed to
grow with unrelated features in parallel, and a hand-maintained derived
number in the same file compounds the risk, because it forces a semantic
merge even when the structural (JSONL line) part is trivially append-only.

## Corrective actions

- `CA-0020` (`docs/EXECUTION-PLAN.md` §3 "Core extension points", landed) —
  declare the core file and the kind of change on the issue before starting;
  prefer append-only additions; whoever lands first sets the base and the
  second lander rebases onto it; a doc that risks independent duplicate
  creation is claimed by filename.
- See `RCA-0017` / `CA-0022` (#1052) for the *second* reason
  `governance/lessons/ledger.jsonl`'s neighbourhood needed a hand merge: the
  README's incident count is now asserted by `scripts/check-lessons.sh`
  against the ledger's actual incident count rather than hand-maintained
  prose.

## Lessons

- `LESSON-0007` — a file that is a legitimate multi-lane extension point
  needs an explicit append-only procedure, not a "one lane = one file"
  exception; and any hand-maintained *derived* number living next to an
  append-only structure should be asserted by a gate instead, so a semantic
  merge is never required for a purely structural append.

## Evidence

- `commit` `6e44c3c431987f007804bd2c772d38eec7ba0f3d` — the
  `docs/EXECUTION-PLAN.md` §3 "Core extension points" section.
- `issue` `#1036` — the PR that reproduced the class against the ledger and
  its README, requiring the hand-merged "Fourteen" union.

## Follow-up

None outstanding.
