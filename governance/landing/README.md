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
                                    gate-status  bash scripts/gate-status.sh post --sha <pr-head>
                                                    --rc <contract-rc>  (ADR-0028 — every outcome,
                                                    before the merge decision)
  4. consult governance/merge's verdict    verdict  re-read the attestation the contract just
                                                    wrote, against the PR's own head commit
  5. print the ordered plan                 landed-contract bash scripts/check-pr-contract.sh
                                                    --landed --range <base>..<head> (the commits to be
                                                    squashed must carry the trailing ticket trailer)
                                            merge   gh pr merge <n> --squash --subject <title>
                                                    --body-file <trailer-bearing message>
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

## The gate-of-record status (ADR-0028, #1072)

The driver publishes the gate of record as a GitHub commit status at the PR
boundary — immediately after the pre-merge contract (`scripts/merge-gate.sh
run`) has run against the pull request's own head commit, and **before** the
merge decision:

```bash
bash scripts/gate-status.sh post --sha <pr-head> --rc <contract-rc>
```

`<contract-rc>` is the contract's own normalised outcome (0/1/2), never a raw
subprocess return code. This is posted for **every** contract outcome, not
just a green one: a red contract publishes `failure`, a CANNOT-ASSESS contract
publishes `error` — so a red commit is decorated red and is never left blank
(the #739 defect class this repo has repeatedly fixed). An attributed
pre-existing red still publishes `failure`: only the *merge decision* is
softened by attribution, never the status posted for the commit.

**A failed or unreadable poster is a named CANNOT-ASSESS refusal**
(`gate-status-unpublished`) — the merge is refused and nothing is merged, no
`landed-contract` check runs, and no PR gets squashed on a commit whose gate
status could not be confirmed published. This is fail-closed by construction:
a poster that could not run is treated exactly like a red gate, never like a
pass.

A dry run **plans** the step (`gate-status`, action `planned`) and posts
nothing — `LandingOps.publish_status` is never called in dry-run mode, the
same discipline every other write in this driver follows.

The step is recorded in `LandingResult.steps` (action `gate-status`) and
carried into `as_dict()` / the `.verify/landing-<n>.json` record and report, so
it is auditable exactly like every other step.

**The escape hatch (ADR-0028's own).** `governance/platform/branch-protection.yaml`
does not yet require the `ao/gate-of-record` context — declaring it a required
check is a separate, deliberate step taken only once a real status has been
observed on a real commit. If the poster ever wedges the merge queue (the
runner stops posting, or `gh`/network is unavailable), the operator escape is
to re-run `bash scripts/branch-protection.sh apply` with
`required_status_checks` removed from the policy file, exactly as ADR-0028
documents — never to bypass this driver's own refusal, which stays
fail-closed by design.

## Pre-existing reds: attributed by measurement, never by a claim

**The defect this closes.** `scripts/merge-gate.sh run`'s `tests` signal runs
`scripts/run-pytest-suites.sh`, and that sweep is **red on clean `origin/master`
itself** — measured on `37f87f9`: 81 passed, **6 failed**
(`governance/modules`, `governance/conformance`, `governance/lessons`,
`integrations/paperclip`, `integrations/paperclip/reporting`, `telemetry/chat`).
A signal no lane can satisfy is a driver that can land nothing, which is why the
previous landing needed a human to merge by hand.

**The route taken — and why.** The repo already has a *declaration* surface for
this: the `## Pre-existing red` section of the PR body, enforced at PR time by
`scripts/check-pr-contract.sh`. That mechanism requires a `Reproduce:` line and a
fenced output block, and its enforcement is a **shape** test — a PR body cannot
be executed, so it cannot distinguish a real reproduction from a fabricated one.
It is therefore *consumed* (this driver writes that section in exactly that
shape, and the gate proves `check-pr-contract.sh` accepts it), but it is
deliberately **not** what grants a waiver: "it fails upstream", asserted by hand,
is a claim. The grant is a measurement.

**The measurement.** `governance/landing/attribution.py` compares the lane's
failing-suite set against the *same* sweep run on clean `origin/master`, taken in
a scratch git worktree and recorded with its provenance (the rev, the commit, when,
and the command — the record and its `measured_sha` must agree or it reads as
malformed). This is the provenanced-baseline discipline of
`scripts/gate-coverage-baseline.txt` (#603/#698), deliberately *not* a committed
row list a lane could extend: because the baseline is keyed by the commit it was
measured at, staleness is structural rather than a promise. A recorded baseline
can be supplied instead of measuring one (`--baseline` /
`AO_LAND_BASELINE_RECORD`) — that changes where the measurement comes from, never
whether there is one.

**The posture — conservative in five ways.**

1. The contract's **own per-signal record** (`.verify/merge-attestation.json`,
the `checks` array) is read **first**. A red with no such record is unattributable
— attributing it would be a guess about *which* signal failed.
2. **`tests` must be the only red signal.** A red `verify` — the gate of record —
is never attributed, whatever the sweep says. The remaining codes are re-scored
through the contract's *own* aggregation (`governance/merge/gate.py`), never
re-decided here.
3. A suite that fails **here** and **passes on clean master** is lane-caused and
refuses, **by name**.
4. A suite with **no verdict** (a timeout) is never attributable, and there is no
grandfathering list: a suite the baseline does not report as failing is refused
immediately.
5. No baseline, an unreadable one, or a sweep record naming **another commit** is
CANNOT-ASSESS — never a grant.

The attributed suites are **named** in the landing output, in the run record
(`.verify/landing-<n>.json`) and in the PR body's `## Pre-existing red` section.
Nothing is dropped silently, and nothing is waived that was not measured.

```bash
# attribute this lane's reds against a freshly measured clean master
python3 -m governance.landing.attribution measure --root . --rev origin/master
python3 -m governance.landing.attribution status  --root . --commit "$(git rev-parse HEAD)"
```

## Tree layout

```text
governance/landing/
├── README.md      # this contract
├── evidence.py    # read + judge the merge attestation (the named gaps)
├── verdict.py     # the ONE seam that loads and consults governance/merge
├── attribution.py # pre-existing suite reds, measured against clean master
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
  file anywhere;
* the **attribution is a measurement**: the same lane and the same baseline give
  opposite verdicts for a suite that fails on both and for one that fails only in
  the lane, and the gate **provokes both constant mutants** — rewriting the
  comparison to always-grant and to always-refuse each changes the answer, with
  the mutated module's `__file__` proven to be the scratch copy, so neither
  rewrite can pass;
* the driver **lands** a lane whose contract is red only on measured pre-existing
  suites, **refuses by name** a lane-caused suite, and **still refuses** when the
  gate of record is red — through the real driver over the fixture stubs;
* the PR body's declaration is accepted by the repo's **own**
  `scripts/check-pr-contract.sh`, run against the fixture lane — so the existing
  mechanism is consumed rather than duplicated.

Non-vacuity: the five pre-flight cases share **one untouched lane** and differ
only in the attestation file handed to `--attestation`; the gate asserts the tree
hash and head commit are identical across them, rejects a refusal that is a
traceback or an argparse error (that is a crash, not a refusal), and pins the
apply order against the fixture's own event log rather than against the driver's
prose. No control ever touches a real repository, and none performs a real merge.

## Known boundaries (reported, not hidden)

* **A red that lives in the WORKING TREE, not in the commit.** The lane-caused
  refusal is deliberately *by the letter*: a suite that fails here and passes on
  clean master refuses, whatever the cause. The driver never decides on its own
  that a red "does not count" — that is the property that must not be traded
  away, and it reports rather than adjudicates. Measured on this lane (#764,
  `7691d8b`): `portal` fails in the lane worktree and **passes in a fresh
  `git worktree` checked out at the very same commit** (357 passed, against 356
  passed + 1 failed in the lane; 402 pass on `origin/master`). The cause is
  `portal/tests/test_live_registry_telemetry.py::_seed`, which selects a seed with
  `next(_SEEDS.glob(f"{profile_id}.*.yaml"))` — **filesystem order** — while the
  loader takes the highest revision: with two published `paperclip` revisions the
  verdict is decided by the order the directory happens to hold, and that order
  differs between a long-lived lane worktree and a fresh checkout. So the driver
  refused this lane, by name, and the remedy belongs to that file's owner — not
  to a waiver here.
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
