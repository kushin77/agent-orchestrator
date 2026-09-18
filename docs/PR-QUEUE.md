# PR-QUEUE.md — the serial squash-merge queue

`scripts/pr-queue.sh` codifies the by-hand PR-queue-clearing procedure done on
2026-09-17 (10 PRs merged in order) as a repeatable, offline-testable tool
(issue #1053, parent #878; see the RCA in `docs/RCA/` or the epic history for
the measured procedure this replaces).

## What it does

Given the set of open pull requests, it classifies each one and prints an
ordered plan. By default it only plans — nothing is merged. With
`AO_QUEUE_APPLY=1` it executes the plan serially, one `gh pr merge --squash`
per PR, re-reading mergeability immediately before each merge.

## Classes

| class            | meaning                                                                    |
|------------------|-----------------------------------------------------------------------------|
| `ready`          | mergeable, no gate path touched — merged first                            |
| `gate-changing`  | mergeable, touches a gate path — merged LAST, after every `ready` PR      |
| `draft`          | excluded unless `AO_QUEUE_INCLUDE_DRAFTS=1` admits it                     |
| `conflict`       | not mergeable against its base — reported, skipped, never fought          |
| `pre-existing-red` | the PR body's `## Pre-existing red` section is not `None` — reported, never auto-merged |
| `unknown`        | `gh` reported no mergeable/mergeStateStatus — reported, never merged      |

A **gate path** is a file whose change affects what later merges must
satisfy: `scripts/verify.sh`, `scripts/gate.sh`, `scripts/merge-gate.sh`,
`scripts/check-*.sh`, `scripts/gate-coverage-baseline.txt` by default.

The printed plan ends with one machine-readable line,
`MERGE_ORDER: <space-separated PR numbers>` — the `ready` and `gate-changing`
PRs, in the order they will be merged. It is part of the tool's contract, not
incidental output: `scripts/check-pr-queue.sh` asserts against it directly, so
it must keep appearing even if the human-readable table above it changes
shape.

## Rules (measured on the night of 2026-09-17)

- Gate-changing PRs land LAST, after every ready PR.
- Drafts are excluded unless explicitly included.
- A PR whose `## Pre-existing red` is not `None` is reported, never
  auto-merged.
- Mergeability is re-read after EVERY merge — master moves, and another
  session can merge or conflict a PR mid-run.
- A conflicting PR is skipped and reported, never fought.
- One call to `gh pr merge --squash` per PR; a refusal stops the run
  immediately — no loop swallowing a refusal.

## Env vars

| var                       | effect                                                                                   |
|---------------------------|-------------------------------------------------------------------------------------------|
| `AO_QUEUE_APPLY=1`        | execute merges serially (default: dry-run plan only), mirroring `AO_LAND_APPLY`          |
| `AO_QUEUE_INCLUDE_DRAFTS=1` | admit draft PRs into the plan and the merge order                                      |
| `AO_QUEUE_GATE_PATHS`     | override the gate-path glob list (whitespace-separated globs)                            |
| `AO_QUEUE_FIXTURE`        | a literal `gh pr list --json ...`-shaped JSON array, or a path to a file holding one — feeds the planner offline, with no `gh` call |

If `scripts/lib/gate-paths.txt` exists (one glob per line), it is used instead
of the inline default when `AO_QUEUE_GATE_PATHS` is unset.

## Usage

```bash
# plan only (default)
bash scripts/pr-queue.sh

# execute for real
AO_QUEUE_APPLY=1 bash scripts/pr-queue.sh

# offline plan against a fixture, no `gh` needed
AO_QUEUE_FIXTURE=/path/to/prs.json bash scripts/pr-queue.sh
```

## Merged-tree evidence (issue #1254 step 6, child of #1254)

Measured 2026-09-18: four times, two PRs each green ALONE were red TOGETHER
— #1110+#1115 (`AO_FROZEN_CLOCK` vs the env-surface gate), #1309+#1287
(`board.freshness` vs control-mapping), #1300 (box-local quarantine), #1246
(RCA doc ids) — because merges were judged on PER-PR-HEAD build results, so
the first build of the ACTUAL merged tree was the NEXT PR's, which
inherited the red silently.

Before `gh pr merge` (both `scripts/pr-queue.sh`'s apply loop and the
single-PR `scripts/merge-pr.sh` entrypoint), the merge now requires evidence
that a verify ran green on a tree equal to `origin/master`'s CURRENT tip
(re-read immediately before merging) plus the PR's head. Evidence sources,
any one suffices:

- **(a) CI status.** The PR head's merge-base is the current master tip
  (`git merge-base origin/master <head>` equals the tip — the base has not
  moved since), and the gate-of-record's own commit status
  (`scripts/gate-status.sh show --sha <head>`) reads success.
- **(b) Local attestation, opt-in.** With `AO_QUEUE_VERIFY_MERGED=1`, the
  queue materializes `origin/master`'s current tip in a detached scratch
  worktree, merges the PR head into it, and runs `scripts/verify.sh verify`
  there (honouring that script's own gate-lock, so it is never a second
  concurrent gate run) — producing `.verify/attestation.json` for that exact
  merged tree, not either side alone.

Otherwise the merge is refused BY NAME, never silently skipped:

| refusal                       | meaning                                                                 |
|--------------------------------|--------------------------------------------------------------------------|
| `merged-tree-unverified:<pr>`  | evidence is stale (the base moved) or absent (no CI status and (b) was not opted into); remedy: `update-branch` / re-run CI, or set `AO_QUEUE_VERIFY_MERGED=1` |
| `merged-tree-red:<check>`      | the merged tree (master tip + this PR's head) itself reds on `<check>`  |

A refusal stops that PR only; the run continues with the next candidate, and
after every successful merge the tip has moved, so the next candidate is
re-judged against the NEW tip — the same re-check discipline the existing
gate-regression check already follows.

`AO_QUEUE_VERIFY_MERGED=1` is opt-in (default: CI status only, or refuse)
because it pays for a real `scripts/verify.sh verify` run per candidate that
lacks fresh CI evidence; `AO_QUEUE_VERIFY_CMD` / `AO_QUEUE_VERIFY_ATTESTATION`
are internal test seams `scripts/check-pr-queue-squash-guard.sh` uses to
prove the red/green paths without paying for a real gate run on every test
invocation, and are not meant for interactive use.

## The gate

`scripts/check-pr-queue.sh` proves the planner offline against a fixture PR
list: a draft is excluded (and admitted on request), a gate-changing PR is
ordered after every ready PR, a conflict and a declared pre-existing red are
both reported and never enter the merge order, and a mutant (the gate-path
glob list emptied) is proven to diverge — the same PR that was gate-changing
becomes ready. It is registered in `scripts/verify.sh` as the `pr-queue`
check and never sets `AO_QUEUE_APPLY=1`.
