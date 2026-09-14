# Reconcile — a dead session leaves a clean workspace

> **Status:** institutional · **Issue:** #304 · **Gate:**
> [`scripts/check-reconcile.sh`](../../scripts/check-reconcile.sh) (`make verify`)
> · **Rule:** `AGENTS.md` golden rule 17

[`governance/isolation`](../isolation/README.md) opens a lane;
[`governance/lifecycle`](../lifecycle/README.md) closes one that finished. This
package handles the third case: a lane whose agent **died**.

## 1. Heartbeats

Every session writes `.fleet/sessions/<session_id>.json` while it runs:

```json
{"session_id": "2c7c76bfc1de", "issue": 304, "agent": "copilot-brain",
 "lane": "governance-reconcile", "worktree": "/home/.../ao-304-f48134aa",
 "branch": "issue-304", "pid": 41234, "at": 1789341000.5,
 "at_iso": "2026-09-13T22:30:00Z", "state": "running", "note": ""}
```

The beat is refreshed on an interval by a `Beater` thread and removed on clean
exit. Writes are atomic (temp file + rename) so a sweep never reads a partial
record and has to guess. The **pid recorded is the process being watched** — for
a dispatched lane that is the subagent, not the loop that spawned it; recording
the loop's pid would make every lane look alive for as long as the loop runs,
which is exactly the signal the sweep needs to be able to lose.

## 2. Two signals, deliberately not one

| Status | When | Reclaimable |
|---|---|---|
| `live` | beat fresh, process alive | no |
| `suspect` | beat fresh, process **gone** | **no** — reported only |
| `orphan` | beat older than the TTL | yes |

Staleness catches a session that is alive but *wedged*, which a pid check cannot
see. Absence is decisive only once the beat has also gone stale: a fresh beat
behind a missing pid is the shape of a handover or a re-exec, and reclaiming a
live lane on that evidence would destroy the work the rule exists to protect.

## 3. Teardown: decided by where the work lives

| Work's location | Action | Outcome |
|---|---|---|
| landed on `master` | remove worktree, delete branch (local + remote), forget lane, release claim, clear beat | `reclaimed` |
| only on a remote branch | remove worktree, **keep** the remote branch, forget lane, release claim, clear beat | `parked` |
| nowhere else | keep everything, mark the session shelved, **keep the claim**, report | `shelved` |

**This worker never trades unmerged work for an unlocked issue.** A lane whose
commits exist only inside its worktree keeps both its worktree and its claim: it
is reported on every pass until someone resolves it, and reclaimed automatically
once its work lands. Unlocking the issue there would invite a second lane to
start the same work while the first lane's only copy sits unmerged — and the
claim's own reaper already exists for the case where the issue must move.

A `parked` or `shelved` lane is **re-evaluated every pass**, never written off.
That is what makes this a worker rather than a one-shot cleanup.

## 4. Usage

```bash
python3 governance/reconcile/cli.py status                        # who is alive
python3 governance/reconcile/cli.py sweep --ttl-minutes 15        # dry run
python3 governance/reconcile/cli.py sweep --ttl-minutes 15 --apply
python3 governance/reconcile/cli.py watch --interval-seconds 60 --apply
python3 governance/reconcile/cli.py watch --once --apply          # one cron tick
```

Exit codes are tri-state: `0` OK, `1` NOT-OK (an orphan is present and nothing was
applied), `2` CANNOT-ASSESS — so a cron tick can tell "the fleet is clean" from
"someone left a lane behind" without parsing prose.

## 5. Enforcement

`scripts/check-reconcile.sh` runs in `make verify` as `reconcile`. It proves the
mechanism against a **real** repository — a scratch repo with a real (local) bare
remote and real linked worktrees — because a worker that deletes branches is only
trustworthy if its refusals are real too. It requires every outcome to be
provoked, including the load-bearing negative: an orphan with unmerged work is
`shelved` and its worktree **survives**.

The bookkeeping steps (`forget-lane`, `release-claim`, `clear-heartbeat`) are
asserted by `governance/reconcile/tests`, since a scratch repository has no
`governance/` of its own to call.

## 6. Board reporting

A finding must reach the board, not only a log line. A `shelved`, `failed` or
`suspect` outcome files a GitHub issue carrying the named violation and its
evidence (`governance/lifecycle/report.py`, issue #321); a previously shelved
lane whose work has since landed is *resolved* — the finding is dropped so a
genuinely new shelve files again.

- **Idempotent.** One issue per fingerprint (the lane's issue number or session
  id), deduped through `.fleet/board-reports.json`, so repeated passes do not
  spam the board for the same unresolved lane.
- **Dry-run by default.** No board write happens without `--apply`; a dry-run
  pass prints `board: dry-run — would file for …`.
- **Offline-testable.** The board effects are an injected port, exercised by
  `governance/reconcile/tests/test_boardreport.py` with no network.

