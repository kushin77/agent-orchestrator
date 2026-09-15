# Landing driver — a green lane lands itself (issue #764)

> Owner lane: **autonomous-ops / governance**. Parent: task #708 (the
> autonomous landing driver). Doctrine: [`AGENTS.md`](../../AGENTS.md)
> (golden rules 1/3/4/5/6/7/12, rule 16 end-to-end closure, the owner
> autonomous-merge mandate of 2026-09-07),
> [`docs/GOLDEN-RULES.md`](../../docs/GOLDEN-RULES.md),
> [`../merge/README.md`](../merge/README.md) (the merge verdict),
> [`../lifecycle/README.md`](../lifecycle/README.md) (hygienic closure).

Landing a verified lane used to be a **manual** sequence: push the branch, open a
PR, run the pre-merge contract, merge, delete the branch, close the issue. Every
delivery therefore ended with "…and then a human pushes and opens the PR", which
is the opposite of this repo's own doctrine: the change path is *declared and
executed by code* (GR-5), and automation is *code-native* — a `make` target run
by the ops runner and cron, never a workflow file (GR-15).

The pieces already existed and were not joined:

| Piece | What it is | What this lane does with it |
|---|---|---|
| `scripts/merge-gate.sh run` | the executable pre-merge contract (issue #29); refuses a dirty tree; writes `.verify/merge-attestation.json` naming the commit; tri-state 0/1/2 | **runs it** — in the lane, with `AO_PR_NUMBER` set, so its `pr-contract` signal enforces the PR body at the PR boundary |
| `governance/merge/` | the merge **decision**: `model.merge_verdict` (green verify + independent reviewer + no-self-merge, with the owner carve-out) driven by `engine.MergeGovernanceEngine` | **consults it** through exactly one seam (`verdict.py`) and carries its answer, reasons and audit trail back verbatim |
| `governance/lifecycle/cli.py close` | hygienic closure (issue #269): merge, evidence, branch delete, directive, claim, lane, issue | **joins it** as the last step and reports its exit code rather than assuming it |

## One command

```bash
make land ISSUE=764                     # DRY RUN: prints the plan, changes nothing
AO_LAND_APPLY=1 make land ISSUE=764     # land it (push -> PR -> contract -> merge -> delete -> close)
```

The equivalent explicit form, with the flags the driver accepts
(`--branch`, `--base`, `--attestation`, `--title`, `--author`, `--notes-file`,
`--ai-assistance`, `--no-owner-carve-out`, `--json`):

```bash
bash scripts/land-lane.sh --issue 764
bash scripts/land-lane.sh --issue 764 --apply
AO_LAND_APPLY=1 bash scripts/land-lane.sh --issue 764
```

Under cron the runner calls `AO_LAND_APPLY=1 make land ISSUE=<n>`; there is no
workflow file and nothing to click (GR-15, GR-5).

## The contract, step by step

```text
  dry run (default)                        apply (--apply / AO_LAND_APPLY=1)
  ─────────────────                        ──────────────────────────────────
  1. read the lane head                    push     git push -u origin <branch>   (never --force)
  2. read the PR for the branch             open-pr  gh pr create --base <base>, body in the
                                                    check-pr-contract shape: Closes #<n>,
                                                    AI-assistance, Pre-existing red, Evidence
  3. judge the pre-flight attestation      contract bash scripts/merge-gate.sh run
                                                    (AO_PR_NUMBER set: the PR body is enforced)
  4. consult governance/merge's verdict    verdict  re-read the attestation the contract just
                                                    wrote, against the PR's own head commit
  5. print the ordered plan                 merge   gh pr merge <n> --squash
  6. change nothing                        delete   git push origin --delete <branch>
                                            close   python3 governance/lifecycle/cli.py close
                                                    --issue <n>
```

1. **Refusal is the default posture.** An attestation that is present and red, or
   green but naming no commit, is refused in *both* modes and **nothing is
   written** — no push, no PR, no merge, no branch delete. Every refusal names
   what it checked (`attestation-not-green`,
   `attestation-does-not-name-a-commit`, `attestation-names-another-commit`,
   `attestation-absent`, …).
2. **The attestation must name the commit being merged.** The commit-naming rule
   is checked twice: from the pre-flight attestation (dry run and pre-flight) and
   again at the merge boundary, against the pull request's own head — the
   evidence the contract just wrote for the tree it just gated. An override cannot
   defeat this: `<root>/.verify/merge-attestation.json` is what the boundary
   reads.
3. **No force, ever.** The only push is `git push -u origin <branch>`; there is
   no `--force` and no `--force-with-lease` anywhere in the driver, no history
   rewrite, and no direct push to a protected branch. A rejected push stops the
   landing and is reported.
4. **Idempotent.** A lane whose pull request is already merged is *terminal*: the
   driver reports the terminal state, performs no landing write, and creates no
   second pull request. An already-open PR is reused, not duplicated. An
   already-published branch is not pushed again.
5. **Never green by assertion.** The driver's own exit code is the honesty
   tri-state (0 OK / 1 NOT-OK / 2 CANNOT-ASSESS): a contract that reds, a
   boundary re-check that fails, an effect that fails, or a closure that does not
   reach its terminal state all make the run NOT-OK — a non-terminal closure is
   *reported*, never traded for a claim.
6. **A dry run writes nothing at all** — no remote change and no local file, so
   "changes nothing" is a property a control can check by looking, not a promise.

## Tree layout

```text
governance/landing/
├── README.md      # this contract
├── evidence.py    # read + judge the merge attestation (the named gaps)
├── verdict.py     # the ONE seam that loads and consults governance/merge
├── ports.py       # the effects: git/gh/contract/closure (real + recording)
├── engine.py      # the ordered landing: order, refusals, idempotence, report
├── cli.py         # tri-state CLI; dry run by default
└── tests/         # pytest suite (offline; injected effects)
scripts/land-lane.sh    # thin entry point (`make land` calls it)
scripts/check-landing.sh # the negative control, wired into `make verify` automatically
```

`scripts/check-landing.sh` is a delivered `scripts/check-*.sh`, so
`scripts/discover-checks.sh` (#698) wires it into `make verify` with **no** edit
to `scripts/verify.sh`.

## What the gate proves (and cannot pass vacuously)

`bash scripts/check-landing.sh` builds **scratch fixtures** — a temp git
repository with a bare origin, a stub `gh`, a stub contract and a stub closure
CLI — and provokes, in the open:

* a red attestation, a green-but-unnamed attestation, an attestation naming
  another commit and a missing attestation are each **refused by name**, with
  nothing pushed, opened, merged or deleted;
* a green attestation naming the head is **granted** in dry run, with the ordered
  plan printed — so the matcher is not simply always refusing;
* **apply, end to end**: the order is proved from one event log, the push is
  proved *server-side* by the origin's own `pre-receive` hook, and the branch
  delete is proved to come after the merge;
* a **second run** on the now-merged lane reports the terminal state, rc 0, with
  no second `pr create` and no second `pr merge`;
* a contract that writes a **stale** attestation is refused at the merge
  boundary, after the push and the PR, with nothing merged;
* a dry run leaves the lane's tree and file listing byte-identical, and writes no
  file anywhere.

Non-vacuity: the five pre-flight cases share **one untouched lane** and differ
only in the attestation file handed to `--attestation`; the gate asserts the tree
hash and head commit are identical across them, rejects a refusal that is a
traceback or an argparse error (that is a crash, not a refusal), and pins the
apply order against the fixture's own event log rather than against the driver's
prose. No control ever touches a real repository, and none performs a real merge.

## Known boundaries (reported, not hidden)

* **Closure inside a lane worktree.** `governance/lifecycle/cli.py close`
  resolves its scope from the *shared* checkout's registries, so in a dispatched
  lane it answers `CANNOT-ASSESS — #<n> is outside the audit scope`. The driver
  reports that exit code unchanged (rc 2) rather than pretending to have closed
  the item; closure is terminal when the ops runner drives it from the checkout
  that holds the lane records. That behaviour belongs to the lifecycle lane
  (#269), and this driver does not paper over it.
* **A PR that auto-closes its issue.** Because the body declares `Closes #<n>`,
  the squash-merge closes the issue, and `gh issue close --comment` (which
  closure uses to post evidence) is then refused with "already closed". The
  driver reports the closure step's failure by name; the remedy — post the
  evidence with `gh issue comment` — belongs to the closure lane.
* **`--ai-assistance` defaults to what the driver can prove.** It declares the
  landing itself (`agent-orchestrator land driver (code-native/cron)`) rather
  than guessing which runtime authored the change; a lane that knows its
  authoring runtime passes `--ai-assistance` or `AO_AI_ASSISTANCE`. The PR-body
  contract at the merge boundary enforces that the line is filled in.
* **`make help` does not list `land`.** `land` was appended to the
  [`Makefile`](../../Makefile) as a pure append (the help list is a hardcoded
  recipe another lane may be editing); this document is its discoverable
  description.
