# The PR-queue-clearing session (2026-09-16) — citing RCA-0007 / RCA-0008

| Field | Value |
|---|---|
| Scope | one session that merged all 17 open PRs against `master` |
| Lessons | two distinct classes, tracked as two RCA ids in this one document |
| Severity | medium (wasted verify-gate throughput + duplicated feature work, no data loss) |
| Owner | governance / gate-wiring lane |
| Reviewed | 2026-09-16 |

This is a lightweight, doc-only RCA. It is **not** entered into the
`governance/lessons/ledger.jsonl` pipeline (see "Why not the lessons
ledger" below) — treat it as the incident writeup, and open a
`governance/lessons` record separately if this session's PRs get real
issue numbers on the board.

## The zero-byte gate-lock wedge (cites RCA-0007)

### Impact

PR #997 (`9e0ab168`, "a zero-byte owner-less gate lock must not wedge lane
reclaim") fixed a defect in `fleet/gatelock.py`'s admission control
(`scripts/verify.sh` acquires a per-worktree lock plus one of
`AO_GATE_MAX_CONCURRENT` box-wide permits, default 4). A worktree whose
holder exited left a lock file behind — 0 bytes on the normal release path,
a stale record when the holder was killed outright — and that leftover was
read as a live claim: `gate-lock status` reported the worktree free while
`gate-lock release` printed "already free" and the file survived, so
automation could never reclaim the lane. The wedge starved the box-wide
4-slot cap, and every OTHER gate waiting on a slot came back
`PARKED (rc 10 / rc 11)` with no indication that one specific leftover file,
in one specific worktree, was the cause.

**Blast radius — measured, not assumed.** The repository's evidence trail
for this incident is not instrumented: `infra/rollout/audit/` has exactly
one file (`2026-09-16-go-live-607-promotion-attempt.md`) that mentions
`PARKED`, and `promotion-audit.jsonl` records no `rc 10`/`rc 11` events at
all. The absence of a counter is itself the second finding here — a wedge
that produces "PARKED" on unrelated PRs' evidence logs, with no ledger that
counts how many PRs or how many hours it cost, is only traceable by manual
correlation after the fact. This RCA does not repeat the "49 concurrent
runs / 43 stacked / ~16 hours" figure from `scripts/check-gate-lock.sh`'s
docstring — that is issue #724's original 2026-09-14 measurement of the
*unbounded-concurrency* problem the admission control itself was built to
fix, not this incident's number, and reusing it here would misattribute it.

### Root cause

`release`'s "already free" path checked `not state.held and
state.record_bytes == 0` and returned success without removing the file.
The *flock*, not the file's bytes, is gate-lock's exclusion primitive
(documented in the module's own docstring — "a 0-byte lock file is never
read as an empty (free) slot"), but nothing enforced that the FILE and the
VERDICT stay in agreement once the flock was gone: a 0-byte leftover could
sit on disk indefinitely, looking exactly like the artifact a live holder
would leave, to any code that read the file's existence or size instead of
taking the flock itself.

### Fix (#997, already landed)

- `release` now reaps a free lock file — taking the flock immediately before
  unlinking, so a genuinely held lock is never touched (`_reap_free_lock`).
- `acquire` re-verifies the flocked inode is still the one at the lock path
  (`_flock_fresh`), closing the unlink-between-open-and-flock race a
  concurrent reap could otherwise create.
- `status` (with or without `--worktree`) now names every worktree lock
  "needing attention" — a stale record, a 0-byte leftover, a held lock with
  an unreadable owner — instead of omitting it.

### Preventive gap this RCA addresses

#997 fixed the **reactive** path: a human (or `status`) has to ask about a
worktree before its leftover is visible. Nothing swept the store
**proactively** — on a schedule, without a human asking — which is exactly
why the wedge cost hours before anyone traced it to one file.

### Guardrail implemented

`fleet/gatelock.health()` / `scripts/gate-lock.sh doctor` (exit 13 on a
finding) runs the same "needing attention" sweep `status()` already
performs, callable from cron or `fleet/watchdog.py`'s existing periodic
pass rather than only on human demand. `watchdog_once()` now runs this
sweep every tick and prints every finding loudly.

**Alert-only, deliberately — this is not auto-heal, and here is why.**
`_reap_free_lock` is safe *only* because the reaper holds the flock on the
exact lock it is about to unlink, so a genuinely live holder is provably
untouched. A box-wide periodic scanner acting on a lock it does not itself
hold — reaping on behalf of a worktree it has no relationship to — is a
different risk class: it is exactly the "delete every file" regression
#997's own commit message names as the failure mode its fix forbids. So the
proactive sweep **reports by name and never removes anything**; the watchdog
prints the finding but deliberately does not fold it into its own pass/fail
verdict, because the sweep is box-wide (it can find a leftover belonging to
a worktree that has nothing to do with the checkout the watchdog is running
in) and folding it in would make an unrelated leftover flip an otherwise
healthy watchdog pass to NOT-OK. `gate-lock.sh doctor`'s own exit 13 is the
enforcement surface for whoever wires it into cron/paging; the watchdog's
copy is the loud, always-on notice.

Auto-reap stays on the one path already proven safe: a worktree's own
`release`.

## The shared-core-file collision class (cites RCA-0008)

### Impact

Three PRs collided in the same queue-clearing pass because independent
lanes extended the same "core" file without knowing about each other, and
landed out of order relative to when they were authored:

- `fleet/watchdog.py` — two logically-complementary alarm/remedy additions.
- `fleet/cron.py` — two independent new-rung additions.
- `docs/FLEET-PARITY.md` — created independently by two lanes.

### Root cause

`docs/EXECUTION-PLAN.md`'s "one issue = one lane = one file set, no two
lanes share a file" doctrine has an explicit exception for genuinely
shared, single-writer build files (`Makefile`, `scripts/verify.sh`,
`scripts/pytest-suites.txt`, `docs/README.md`,
`scripts/gate-coverage-baseline.txt` — RC-8, #559), but no rule at all for
files that are **legitimate extension points for more than one feature at
once** — `fleet/watchdog.py` (any new alarm type), `fleet/cron.py` (any new
rung). Unlike the RC-8 files, nobody should be a permanent single writer of
these: they are supposed to grow with unrelated features in parallel. The
doctrine's silence on "how do two lanes safely extend the same core file"
meant the two collisions were discovered at merge time instead of before
either lane started.

### Guardrail implemented

`docs/EXECUTION-PLAN.md` §3 now has a **"Core extension points"** section,
modeled on the existing RC-8 / contract-freeze paragraphs in the same
section: declare the core file and the kind of change on the issue before
starting; prefer append-only additions (a new function/dict-entry/heading,
never an interleaved edit); whoever lands first sets the base and the
second lander rebases onto it rather than resolving a conflict blind; and a
doc that risks independent duplicate creation is claimed by filename the
same way. This intentionally stays prose-plus-the-existing-claim-CLI rather
than a new lock registry, matching the doctrine's own stated preference for
staying lightweight.

## Evidence

- `git show 9e0ab168` — the #997 fix, and the module docstring it edited.
- `fleet/gatelock.py::health`, `fleet/gatelock.py` CLI `doctor` subcommand.
- `fleet/tests/test_gatelock.py` — health-sweep tests, including a
  never-deletes-what-it-finds assertion.
- `fleet/watchdog.py::watchdog_once` — the per-tick sweep + loud print.
- `docs/EXECUTION-PLAN.md` §3 — the core-extension-point rule.

## Why not the `governance/lessons` ledger

`governance/lessons/README.md` documents the repo's real RCA pipeline —
`RCA-<n>` artifacts recorded against a real `INC-<n>` in
`ledger.jsonl`, each `origin` resolved against the committed board
snapshot, checked by `governance/lessons/cli.py check`. That pipeline
requires a real incident/issue/PR reference resolvable on the board; this
session's "17 PRs" and the specific collisions named above were given as
narrative context rather than a set of board-resolvable refs, so recording
them there would plant unverifiable references into an otherwise-verified
ledger. This document captures the same analysis and the same corrective
actions without that risk. If/when this incident gets real issue numbers,
promote it into the ledger with `governance/lessons/cli.py record`.
