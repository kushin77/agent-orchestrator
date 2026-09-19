# RCA-0019 — the PR-runner prototype day: ten ways an unattended verify-and-merge loop goes wrong

| Field | Value |
|---|---|
| RCA id | `RCA-0019` |
| Incident | `INC-0019` |
| Origin | event `pr-runner-prototype-2026-09-18` (issue #1343, parent #1295) |
| Severity | high |
| Owner | fleet lane (`fleet/runner/`) |
| Reviewed | 2026-09-18 |

## Impact

On 2026-09-18 the shared-services pair (192.168.168.42) ran a prototype PR
runner as two nohup shell scripts in `~/ao-runner` (`host-verify.sh`,
`host-merge.sh`). Over the day it (1) spent ~40% of its verify capacity on
heads that had already been pushed over, because evidence was recorded per PR
rather than per `(pr, sha)`; (2) waited on EXPIRED and CANCELLED builds as if
they would finish (#1267 class); (3) merged four PRs that were green alone and
red together on master (#1254 step 6); (4) invoked a bare `gh pr merge` that
consulted no squash guard (#1266); (5) counted host-environment reds (docker
compose, AR credentials, the auth-gate env) against heads Cloud Build had
proven green, and could not name a missing precondition (#1313); (6) lost
in-flight verifies when `scripts/prune-worktrees.sh` reaped its detached
scratch worktrees under the content-equivalence rule (#1335); (7) leaked
gate-lock permits every time a run was killed; (8) raced its own fetches on
one clone (`cannot lock ref`) and lacked `refs/remotes/origin/master` on a
detached checkout; (9) died with its supervisor, wrote no ledger and could not
answer "what are you verifying, what merged, what is blocked and why"; and
(10) depended on a required-check context that nothing was guaranteed to post.

## Detection

Measured by the operator reading the prototype's logs and the merge history
on the pair during the day (issue #1343 records each measurement); the four
green-alone-red-together merges surfaced as master reds after the fact.

## Root cause

The prototype was a pair of shell loops with no model: no single key for
evidence, no distinction between "finished not green" and "still running", no
merged-tree check at merge time, no guarded merge verb, no ranking of evidence
sources, no ownership of its own worktrees and locks, no serialised fetch, no
ledger and no declared schedule. Each lesson below is one missing invariant.

## Corrective actions

- `CA-0024` (issue #1343) — `fleet/runner/` replaces the prototype: a pure
  planner over per-`(pr, sha)` ranked evidence (`plan.py`, `evidence.py`),
  transports that hold and remove their worktrees, prune gate-lock permits,
  serialise fetches with explicit refspecs, post through
  `scripts/gate-status.sh` and ledger every step (`verify.py`), a merge that
  consumes `scripts/pr-queue.sh --check-merged-tree` and `scripts/merge-pr.sh`
  only (`merge.py`), a `status` verb, a role-gated rung in
  `config/fleet-jobs.json` / `fleet/cron.py`, and `scripts/check-pr-runner.sh`
  which provokes every lesson and two mutants.

## Lessons

Closed (each proven by a landed master commit that ships its foundation, plus
PR #1356 which adds the runner's own control):

- `LESSON-0009` — evidence is per `(pr, head sha)`, never per PR: the gate of
  record is a status on the exact commit (`ffc446aa`, ADR-0028; `335c9a34`,
  #1342); a pushed head invalidates all prior evidence and stale builds are
  cancelled.
- `LESSON-0010` — never a bare merge command: `scripts/merge-pr.sh` is the one
  verb, the squash guard runs first, the trailer and `Closes #<n>` are its
  business (`9b315f5a`, `a38a5c91`, #1233).
- `LESSON-0011` — a runner that cannot reach a precondition reports
  CANNOT-ASSESS by name and is never counted green (`9f62c0d6`, #1313);
  evidence sources are ranked and all recorded, so a host-env red cannot block
  a Cloud Build green.
- `LESSON-0012` — a detached scratch worktree must be held (open fd) for the
  whole run and removed by its owner (`83ff32db`, #1335; `0dbb0e68`, #1159).
- `LESSON-0013` — gate-lock permits leak when a run is killed: prune with
  `fleet/gatelock.py prune --apply` before each cycle (`8a4df3ad`, #1170).
- `LESSON-0014` — a required check whose context nothing posts blocks everyone
  forever: the poster and the protection name one string (`335c9a34`,
  `ffc446aa`).

Open (the control lands only in PR #1356 / #1332; each closes as a `LESSON`
on the landed sha, per its `remediation`):

- `SUGGEST-0014` — EXPIRED / CANCELLED / PARKED are terminal-not-green:
  re-queue by name, never wait.
- `SUGGEST-0015` — merge only on green evidence for master + PR at the moment
  of merging, consumed from the one merged-tree seam (#1332).
- `SUGGEST-0016` — parallel fetches on one clone race: serialise them under a
  lock and name explicit refspecs.
- `SUGGEST-0017` — a loop must survive its supervisor (a declared rung), log
  to a ledger and expose a status verb.

## Evidence

- `pr` `#1356` — `fleet/runner/` (planner, ranked evidence, held worktrees,
  fetch lock, gate-lock prune, ledger + status, merged-tree seam + guarded
  verb), `scripts/check-pr-runner.sh`, the role-gated rung, and the named
  controls in `fleet/runner/tests/`.
- `commit` `ffc446aaa6f8c509bf746c03360f1a76d4ee2623` — ADR-0028: the gate of
  record as a commit status per sha (#810).
- `commit` `335c9a347d44d9d420eb21208d90772953acc68b` — `ao/gate-of-record`
  required on master (#1342).
- `commit` `9b315f5a5669eced4a41bd4cbd8a6472c745ab9f`,
  `a38a5c91ca9f47e926bfd4c677c984e66ac08a91` — the one guarded merge
  entrypoint and the instruction that names it (#1233).
- `commit` `9f62c0d6c18f9051fe8c108a2b60f47149085e6a` — CANNOT-ASSESS by name
  when a precondition is absent (#1313).
- `commit` `83ff32dbcf40fe42c4ccbdcb3a41e5b4a48944f6`,
  `0dbb0e6819979e329b615292adaa74ceecded5ee` — the reaper's content-equivalence
  rule and its open-file liveness test (#1335, #1159).
- `commit` `8a4df3ad8e7e9a669a4c7b7dee3599218c2ccd4f` — provably-safe gate-lock
  prune (#1170).
