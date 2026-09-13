# RCA-0001 — a gate merged before the text it asserts (false green)

| Field | Value |
|---|---|
| RCA id | `RCA-0001` |
| Incident | `INC-0001` |
| Origin | `#153` — PR that added the chronological-dispatch gate |
| Severity | `high` |
| Owner | governance lane |
| Reviewed | `2026-09-13` |

## Impact

`master` was red for every clone, agent and clean checkout from the merge of
PR #153, while the author's own `make verify` reported `PASS (9 of 9 checks)`.
Two subsequent PRs were needed to make the tree green again, and every lane that
synced in the window inherited a failing gate of record.

## Detection

Late and by hand: the next agent to run the gate on a **clean checkout** saw
`rc=1` with `3 of 3 contract doc(s) non-conforming`. The author's working tree
could not have detected it — the rule text the gate asserted was an uncommitted
edit sitting in that same working tree.

## Root cause

The gate and its premise landed in the wrong order. `scripts/check-`
`chronological-dispatch.sh` asserts that `AGENTS.md`, `docs/GOVERNANCE.md` and
`docs/EXECUTION-PLAN.md` declare the dependency-ordered selection rule
(GR-20) — but the declaration was never committed. A dirty working tree can
satisfy a gate's own precondition, so the gate's green was manufactured by the
one condition that would not survive the merge (GR-12: a check must be able to
fail on what actually lands).

## Corrective actions

- `CA-0001` — ship the missing declaration text and the corrected doc count.
  Landed in PR #155 (`e78f80b`, 53 insertions across the three contract docs).
- `CA-0002` — harden the script so a non-conforming document is reported per
  document rather than through a shared counter. Landed in PR #156 (`0ba7a2c`,
  13 insertions in `scripts/check-chronological-dispatch.sh`).

## Lessons

`LESSON-0001` — prove any new gate against the **committed** tree before merge.
Reconstruct the files the gate reads from the merge commit in a scratch
directory and run the gate there; a green working tree is not evidence. The
reconstruction recipe now lives in the repo's gate notes and in
[`../README.md`](../README.md).

## Evidence

- PR #155 — `e78f80b` (`git diff --stat e78f80b^ e78f80b`: 3 files, 53
  insertions) shipped the missing doc text.
- PR #156 — `0ba7a2c` (1 file, 13 insertions): the gate now counts conforming
  documents correctly.
- The measured reproduction: gate `rc=1`,
  `3 of 3 contract doc(s) non-conforming` on a clean checkout of the merge
  commit.

## Follow-up

Nothing outstanding on the incident itself. The durable improvement — a
clean-checkout proof step for every new gate — is recorded as `SUGGEST-0001` on
this RCA and is still open.
