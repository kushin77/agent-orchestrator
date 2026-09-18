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

## The gate

`scripts/check-pr-queue.sh` proves the planner offline against a fixture PR
list: a draft is excluded (and admitted on request), a gate-changing PR is
ordered after every ready PR, a conflict and a declared pre-existing red are
both reported and never enter the merge order, and a mutant (the gate-path
glob list emptied) is proven to diverge — the same PR that was gate-changing
becomes ready. It is registered in `scripts/verify.sh` as the `pr-queue`
check and never sets `AO_QUEUE_APPLY=1`.

## The PR contract (issue #1254 step 5 / #1328)

Every PR body carries a `## Classification` block (`.github/PULL_REQUEST_TEMPLATE.md`,
placed directly under `## Closes`): `class`, `posture`, `lifecycle`, `pillar`,
`pattern`, `lane`, one `key: value` per line, closed vocabularies. It is a
projection of the same tag authority `governance/tagging/cli.py` already
enforces on issues (`class` from `governance/conformance/policy.yaml`'s ladder,
`posture`/`lifecycle`/`pillar` from `governance/tagging/taxonomy.yaml`,
`pattern` resolved against `docs/PYTHON-PATTERNS.md` / `docs/SHELL-PATTERNS.md`
/ `AGENTS.md` golden rules / `docs/decision-records/` ADRs) — never a second
copy of any of those vocabularies.

`bash scripts/check-pr-contract.sh --pr <N>` reads the block and refuses, by
name: `pr-classification-missing`, `pr-class-unknown`, `pr-posture-unknown`,
`pr-lifecycle-unknown`, `pr-pillar-unknown`, `pr-posture-contradiction`
(`no-human-needed` + `human-gated` together), `pr-pattern-unresolvable`,
`pr-lane-mismatch` (`lane: issue-<n>` disagreeing with the head branch), and
`pr-class-below-surface` (the declared `class` sits below the rung
`governance/conformance/surfaces.yaml` declares for a surface root the PR's
diff touches — via `governance/conformance/surfaces.py`'s own loader). A PR
with no `--pr` context, and neither `$_PR_NUMBER` nor `$PR_NUMBER` set, cannot
be assessed and exits 2 naming `pr-context-missing`.

**Warn-only today.** These classification findings print but do not flip the
exit code: `AO_PR_CONTRACT_ENFORCE=1` is what turns them into a hard refusal
(rc 1). The existing trailer/`Closes`/`AI-assistance`/`Gate-changing`/
pre-existing-red checks are unaffected and keep enforcing unconditionally.
Enforcement flips in a later PR, once every open PR carries the block —
flipping it early would red every PR opened before this one merged.

`python3 governance/tagging/cli.py pr-labels --pr N` derives the
`class:`/`posture:`/`lifecycle:`/`pillar:` labels the block implies; `--apply`
sets them on the PR via `gh`. A hand-applied label that disagrees with the
body is reported as `pr-label-drift`, never silently trusted.

Self-test both gates directly:

```bash
bash scripts/check-pr-contract.sh --selftest
python3 -m pytest governance/tagging/tests -k pr_labels
```

**Wired into the runner (issue #1341).** `scripts/verify.sh` is discovered by
`scripts/check-pr-contract.sh` (a plain `scripts/check-*.sh`) already; a local
`make verify` sets no PR context, so the check answers its own rc 2
`pr-context-missing` there — a SKIP, never a fail. On the Cloud Build verify
trigger (`infra/cloudbuild/verify.yaml`), the runner's own `$_PR_NUMBER`
substitution is exported as `AO_PR_NUMBER`, and `scripts/verify.sh` forwards it
to `PR_NUMBER` before the discovered checks run — so on a PR build the gate
runs with real PR context (still warn-only, per the enforcement flag above).
