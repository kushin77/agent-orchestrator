# Reconcile orphan-lane triage (#1602) — the blocked set was never "merged PRs missing evidence"

## Scope

Issue #1602 (parent #1510) asked to triage the lanes whose
`governance/lifecycle/cli.py close --lane <id>` refused with
`closeout-blocked:<reason>`, so that the orphan walk's `orphan-issue-lane`
count could fall toward its budget. It carried two lists measured
2026-09-20:

- **`closeout-blocked:pr-merged` (28 lane ids, "25")** — the issue's premise
  was "PR merged but the close-out's PR-merged evidence step isn't satisfied
  (trailer / attestation gap, needs per-lane investigation)".
- **`closeout-blocked:branch-reaped` (12 lane ids, "16")** — "local branch is
  not content-landed on the default branch; `close --lane` keeps it rather
  than deleting unlanded work".

The issue's ask was to supply the missing trailer/attestation evidence for the
`pr-merged` group, and for the `branch-reaped` group to land, discard (with
sign-off) or re-open per branch.

## Method

Every number below was re-measured on 2026-09-21 against the live box, with the
lane's own checkout (`/home/akushnir/ao-worktrees/ao-1602-1444e90d`) and the
shared checkout's git state (`/home/akushnir/agent-orchestrator`,
`origin/master` = `5dd43d09`):

- **Tool verdict** — `AO_LIFECYCLE_ROOT=<shared checkout> python3
  governance/lifecycle/cli.py close --lane <id> --json` (dry run, per id).
- **Local branch** — `git for-each-ref refs/heads`.
- **Remote branch** — `git ls-remote origin refs/heads/<branch>`.
- **Content-landed** — the tool's own predicate,
  `governance.isolation.worktree.content_landed(<tip>, "origin/master")`.
- **PR state** — `gh api repos/kushin77/agent-orchestrator/pulls/<n>` for each
  PR the tool named.
- **Issue state** — `gh api repos/.../issues/<n>`.

`close --lane` was run for **all 40** ids. Not one of the 40 returned
`"ok": true`, so the `reclaimable-now` set is empty and nothing could be
applied under the issue's own `--apply` rule.

## Finding

Two findings overturn the issue's premise:

1. **No `pr-merged` lane has a merged PR.** All 22 PRs the tool named are
   `state=closed`, `merged=false`, `merged_at=null`. They were **closed without
   merging**, not merged-with-a-trailer-gap. There is no attestation or
   trailer to supply: the work either landed by another route (5 lanes are
   content-landed on `master`) or did not land at all. The label
   `closeout-blocked:pr-merged` is the step NAME, not a statement that a merge
   happened.
2. **No lane is reclaimable now.** Every one of the 40 still refuses, 20 of
   them for **unlanded work** — exactly the case rule 17 forbids trading for an
   unlocked issue. Reclaiming any of them would delete or hide work whose tip
   is not on `master`.

## Per-lane measurements

`wt(admin)` = the worktree is a live git admin entry; `local br` = the local
branch exists; `remote br` = the branch is on `origin`; `content-landed` = the
lane's predicate against `origin/master` (for a lane whose local branch is
gone, the figure is for the **preserved remote tip**).

| lane | issue | group | wt(admin) | local br | remote br | content-landed | tool verdict | disposition |
|---|---|---|---|---|---|---|---|---|
| `0dcc14d95072` | #1410 | pr-merged | no | no | yes | no (remote tip) | closeout-blocked:pr-merged | needs-operator-decision (parked on remote, not landed) |
| `0f9514896d3c` | #1416 | pr-merged | yes | yes | yes | yes | closeout-blocked:pr-merged | blocked:pr-merged (content landed) |
| `17e6d18bcd3c` | #1467 | pr-merged | yes | yes | yes | yes | closeout-blocked:pr-merged | blocked:pr-merged (content landed) |
| `2087a08af197` | #1412 | pr-merged | no | no | yes | no (remote tip) | closeout-blocked:pr-merged | needs-operator-decision (parked on remote, not landed) |
| `4b36d36383b6` | #1414 | pr-merged | no | no | yes | yes (remote tip) | closeout-blocked:pr-merged | already-reclaimed |
| `502bad45d42d` | #1459 | pr-merged | yes | yes | yes | no | closeout-blocked:pr-merged | needs-operator-decision (unlanded) |
| `53902bbadfc1` | #1407 | pr-merged | no | no | yes | no (remote tip) | closeout-blocked:pr-merged | needs-operator-decision (parked on remote, not landed) |
| `5483048937dc` | #1389 | pr-merged | no | no | yes | yes (remote tip) | closeout-blocked:pr-merged | already-reclaimed |
| `58c69aec9f5f` | #1404 | pr-merged | no | no | yes | yes (remote tip) | closeout-blocked:pr-merged | already-reclaimed |
| `5e4b818bb55f` | #1369 | pr-merged | yes | yes | yes | no | closeout-blocked:pr-merged | needs-operator-decision (unlanded) |
| `618f765142ce` | #1415 | pr-merged | yes | yes | yes | no | closeout-blocked:pr-merged | needs-operator-decision (unlanded) |
| `6f26182060c6` | #1411 | pr-merged | no | no | yes | no (remote tip) | closeout-blocked:pr-merged | needs-operator-decision (parked on remote, not landed) |
| `84d6abee0581` | #967 | pr-merged | no | yes | yes | no | closeout-blocked:pr-merged | needs-operator-decision (unlanded) |
| `858b8e032699` | #1441 | pr-merged | no | no | yes | yes (remote tip) | closeout-blocked:pr-merged | already-reclaimed |
| `8cdd83436eb1` | #1424 | pr-merged | no | no | yes | yes (remote tip) | closeout-blocked:pr-merged | already-reclaimed |
| `8d7b3f8d982f` | #1444 | pr-merged | no | yes | yes | no | closeout-blocked:pr-merged | needs-operator-decision (unlanded) |
| `947e27012bd1` | #1412 | pr-merged | yes | yes | yes | no | closeout-blocked:pr-merged | needs-operator-decision (unlanded) |
| `956dd8b0147c` | #1436 | pr-merged | no | no | yes | yes (remote tip) | closeout-blocked:pr-merged | already-reclaimed |
| `9c3cc8b88c62` | #1392 | pr-merged | yes | yes | yes | yes | closeout-blocked:pr-merged | blocked:pr-merged (content landed) |
| `a65d08ed6386` | #1412 | pr-merged | yes | no | no | n/a | closeout-blocked:pr-merged | needs-operator-decision (record names `issue-1412-c`; worktree is on `issue-1412-combined` @ `dfd19b42`, not landed) |
| `b1385a5ed14f` | #1402 | pr-merged | no | no | yes | yes (remote tip) | closeout-blocked:pr-merged | already-reclaimed |
| `ca0d68ffd9cb` | #1264 | pr-merged | no | yes | yes | no | closeout-blocked:pr-merged | needs-operator-decision (unlanded) |
| `cea02aec5679` | #1440 | pr-merged | yes | yes | yes | yes | closeout-blocked:pr-merged | blocked:pr-merged (content landed) |
| `d7e444bbeae3` | #1360 | pr-merged | no | no | yes | no (remote tip) | closeout-blocked:pr-merged | needs-operator-decision (parked on remote, not landed) |
| `e4d69fbdb3c7` | #1413 | pr-merged | no | no | yes | yes (remote tip) | closeout-blocked:pr-merged | already-reclaimed |
| `e94dd26a2c3c` | #1437 | pr-merged | no | no | no | n/a | closeout-blocked:pr-merged | needs-operator-decision (branch lost — nothing on disk or remote) |
| `f96091bb3a1e` | #1199 | pr-merged | no | yes | no | no | closeout-blocked:pr-merged | needs-operator-decision (unlanded, local-only) |
| `fe2fc174ef8a` | #1471 | pr-merged | yes | yes | yes | yes | closeout-blocked:pr-merged | blocked:pr-merged (content landed) |
| `015c115d9f61` | #1270 | branch-reaped | yes | yes | yes | no | closeout-blocked:branch-reaped | needs-operator-decision (unlanded) |
| `095b6256ab43` | #777 | branch-reaped | no | yes | no | no | closeout-blocked:branch-reaped | needs-operator-decision (unlanded, local-only) |
| `19816f9db529` | #1028 | branch-reaped | no | yes | no | no | closeout-blocked:branch-reaped | needs-operator-decision (unlanded, local-only) |
| `19aca47635cb` | #1311 | branch-reaped | no | yes | yes | no | closeout-blocked:branch-reaped | needs-operator-decision (unlanded) |
| `5ba3c12c4bdb` | #1345 | branch-reaped | yes | yes | yes | no | closeout-blocked:branch-reaped | needs-operator-decision (unlanded) |
| `88e06a819a8c` | #1338 | branch-reaped | yes | yes | yes | no | closeout-blocked:branch-reaped | needs-operator-decision (unlanded) |
| `891188da4ba8` | #1159 | branch-reaped | no | yes | yes | no | closeout-blocked:branch-reaped | needs-operator-decision (unlanded) |
| `95a6c7623c9a` | #1310 | branch-reaped | no | yes | yes | no | closeout-blocked:branch-reaped | needs-operator-decision (unlanded) |
| `d029f658bdb5` | #1382 | branch-reaped | yes | yes | yes | no | closeout-blocked:branch-reaped | needs-operator-decision (unlanded) |
| `dc4015fbc4b7` | #1338 | branch-reaped | no | yes | yes | no | closeout-blocked:branch-reaped | needs-operator-decision (unlanded) |
| `ef996e6db563` | #1351 | branch-reaped | yes | yes | no | no | closeout-blocked:branch-reaped | needs-operator-decision (unlanded, local-only) |
| `fcd6ad6948fd` | #1233 | branch-reaped | no | yes | yes | no | closeout-blocked:branch-reaped | needs-operator-decision (unlanded) |

## Dispositions

| disposition | count | lanes |
|---|---|---|
| `already-reclaimed` | 8 | `4b36d36383b6`, `5483048937dc`, `58c69aec9f5f`, `858b8e032699`, `8cdd83436eb1`, `956dd8b0147c`, `b1385a5ed14f`, `e4d69fbdb3c7` |
| `reclaimable-now` | 0 | — |
| `blocked:pr-merged` (content landed) | 5 | `0f9514896d3c`, `17e6d18bcd3c`, `9c3cc8b88c62`, `cea02aec5679`, `fe2fc174ef8a` |
| `needs-operator-decision` | 27 | the remaining 27 ids (below) |

`needs-operator-decision` breaks down as:

- **18 unlanded, local branch intact** — the diff is preserved on the local
  branch (and on `origin` for 14 of them) but not on `master`: every
  `branch-reaped` lane (12) plus `502bad45d42d`, `5e4b818bb55f`,
  `618f765142ce`, `84d6abee0581`, `8d7b3f8d982f`, `947e27012bd1`,
  `ca0d68ffd9cb`, `f96091bb3a1e`. Each needs a decision: land it, discard it
  with sign-off, or re-open its issue.
- **5 parked on `origin` only** (local worktree and branch already gone, the
  remote tip is **not** content-landed): `0dcc14d95072`, `2087a08af197`,
  `53902bbadfc1`, `6f26182060c6`, `d7e444bbeae3`. The work survives on the
  remote branch; landing it is the only path that reclaims the record safely.
- **1 branch lost** (`e94dd26a2c3c` #1437): no local branch, no remote branch,
  no PR. The record is the only remaining trace; it cannot be measured and must
  not be archived.
- **1 stale record** (`a65d08ed6386` #1412): the record names branch
  `issue-1412-c`, but its registered worktree is checked out on
  `issue-1412-combined` @ `dfd19b42`, which is not content-landed. The three
  `#1412` records (`2087a08af197` beats, `947e27012bd1`, `a65d08ed6386`) all
  bind an open issue's work.

### The two `already-reclaimed` sub-cases, and the dangling dirs

The 8 `already-reclaimed` lanes have **no** local branch and **no** worktree
admin entry; their preserved remote tip **is** content-landed, so nothing is at
risk and they are reclaimed in fact — only the `.fleet/lanes/<id>.json` record
remains, and the verb will not archive it while the `pr-merged` step is
blocked. Two further lanes (`2087a08af197`, `6f26182060c6`) have a **dangling
directory** at their recorded worktree path that is not a git worktree entry
(the #1443 shape); a third (`a65d08ed6386`) still has a live admin entry.

## Applied

**Nothing.** `close --lane --apply` was run for zero lanes: the dry run did not
return `"ok": true` for any of the 40, and the task's rule is to apply only the
`reclaimable-now` subset. `--apply` on a blocked lane is also not inert — its
help text says it "file[s] remaining findings on the board" — so it was not run
on any refused lane either.

## Budget state (reported honestly)

`sweep --orphans` (budget from `governance/reconcile/orphan-budget.yaml`,
budget=36/95/20/25/115) is **still exceeded**, and `orphan-issue-lane` **grew**
rather than shrank since the issue was written:

| kind | now | budget | vs issue's 2026-09-20 figure |
|---|---|---|---|
| `orphan-worktree` | 64 | 36 | (issue did not cite) |
| `orphan-branch` | 126 | 95 | issue: 97 |
| `orphan-pr` | 0 | 20 | — |
| `orphan-issue-lane` | 53 | 25 | **issue: 42 — grew by 11** |
| `orphan-directive` | 54 | 115 | issue: 54 |

`scripts/check-reconcile-orphans.sh --skip-real-tree` is rc 0 (its §4 real-tree
budget check is the part `--skip-real-tree` skips; the `sweep --orphans` reds
above are that check's finding, not a gate failure).

The 40 lanes in this issue are 53-ish of the `orphan-issue-lane` pile; clearing
them by evidence would drop the count toward budget, but **none of the 40 is
clearable by evidence today** — see the dispositions. `orphan-issue-lane` will
keep rising as settled lanes are reaped without their records being archivable,
because the lane verb cannot retire a record whose `pr-merged` step is blocked
by a closed-unmerged PR.

## Deliberately not done (rule 17 — never trade unlanded work)

- **Did not run `sweep --orphans --apply`.** Its reclaim set is not
  liveness-aware (#1440), and it is not scoped to these 40 lanes; it would have
  touched live peer venues and `would-reclaim` entries outside this issue.
- **Did not archive any `.fleet/lanes` record.** For a record with no branch and
  no worktree, archiving would erase the only pointer to work that may be
  unlanded (and, for `a65d08ed6386`, misnames the branch). Archiving is an
  operator decision, not a triage side effect.
- **Did not use `close --issue <n>`** to force the 5 content-landed lanes
  through a different verb. That is the operator's route (the tool's own message
  offers it), not this lane's; this lane applies `close --lane` only.
- **Did not delete, force-remove, or `--apply` a reclaim of any** worktree or
  branch whose content is not proven landed on `master` — including the two
  dangling directories, which are kept as evidence pointers.
- **Did not `git fetch`/write.** All git reads were `for-each-ref`,
  `rev-parse`, `ls-remote` and `worktree list`; the only ref movement was the
  tooling's own `close --lane` fetch of `origin/master`.
