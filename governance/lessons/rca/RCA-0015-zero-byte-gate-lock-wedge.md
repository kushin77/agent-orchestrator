# RCA-0015 — the zero-byte gate-lock wedge

| Field | Value |
|---|---|
| RCA id | `RCA-0015` |
| Incident | `INC-0015` |
| Origin | `#997` — the fix PR; the wedge itself predates a tracked issue number |
| Severity | medium |
| Owner | governance / gate-wiring lane |
| Reviewed | 2026-09-17 |

This promotes the first of the two lessons from the lightweight writeup
[`docs/rca/2026-09-16-pr-queue-clearing.md`](../../../docs/rca/2026-09-16-pr-queue-clearing.md)
into the canonical ledger (issue #1052): that document titled itself
`RCA-0007 / RCA-0008`, double-booking ids the ledger had already minted for a
different incident (`INC-0007`/`INC-0008`). `RCA-0015` is the ledger's own,
single-authority id for this content; the doc now cites it instead of minting
it.

## Impact

PR #997 (`9e0ab168`, "a zero-byte owner-less gate lock must not wedge lane
reclaim") fixed a defect in `fleet/gatelock.py`'s admission control
(`scripts/verify.sh` acquires a per-worktree lock plus one of
`AO_GATE_MAX_CONCURRENT` box-wide permits, default 4). A worktree whose
holder exited left a lock file behind — 0 bytes on the normal release path, a
stale record when the holder was killed outright — and that leftover was read
as a live claim: `gate-lock status` reported the worktree free while
`gate-lock release` printed "already free" and the file survived, so
automation could never reclaim the lane. The wedge starved the box-wide
4-slot cap, and every OTHER gate waiting on a slot came back
`PARKED (rc 10 / rc 11)` with no indication that one specific leftover file,
in one specific worktree, was the cause.

## Detection

A human traced repeated `PARKED` verdicts on unrelated PRs back to one
worktree's leftover lock file by manual correlation; nothing counted or
surfaced the wedge on its own. The repository's evidence trail is not
instrumented for it: `infra/rollout/audit/` had exactly one file mentioning
`PARKED`, and `promotion-audit.jsonl` recorded no `rc 10`/`rc 11` events at
all — the absence of a counter is itself part of this incident.

## Root cause

`release`'s "already free" path checked `not state.held and
state.record_bytes == 0` and returned success without removing the file. The
*flock*, not the file's bytes, is gate-lock's exclusion primitive, but
nothing enforced that the FILE and the VERDICT stay in agreement once the
flock was gone: a 0-byte leftover could sit on disk indefinitely, looking
exactly like the artifact a live holder would leave, to any code that read
the file's existence or size instead of taking the flock itself.

## Corrective actions

- `CA-0019` (#997, landed) — `release` reaps a free lock file by taking the
  flock immediately before unlinking (`_reap_free_lock`); `acquire`
  re-verifies the flocked inode is still the one at the lock path
  (`_flock_fresh`); `status` names every worktree lock "needing attention"
  instead of omitting it; `fleet/gatelock.health()` / `gate-lock.sh doctor`
  (exit 13) runs the same sweep proactively, wired into
  `fleet/watchdog.py`'s periodic pass, alert-only by design (a box-wide
  sweep reaping a lock it does not own is the regression #997's own fix
  forbids).

## Lessons

- `LESSON-0006` — a lock's release path must verify the exclusion primitive
  it actually holds (the flock), never a proxy for it (file bytes, file
  existence); a periodic health sweep that only *reports* is a safe
  complement to a reap that only the lock's own holder performs.

## Evidence

- `commit` `9e0ab1683777aa5995130832978b4006dcf07fcd` — the #997 fix
  (`_reap_free_lock`, `_flock_fresh`).
- `commit` `6e44c3c431987f007804bd2c772d38eec7ba0f3d` — `gatelock.health()` /
  `gate-lock.sh doctor` and the `fleet/watchdog.py` wiring.
- `fleet/tests/test_gatelock.py` — health-sweep tests, including a
  never-deletes-what-it-finds assertion.

## Follow-up

None outstanding; the doctor sweep is alert-only by design (see Root cause) —
auto-reap stays scoped to a worktree's own `release`.
