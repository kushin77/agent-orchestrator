# Fleet parity — the acceptance test as a runnable instrument

Issue **#799**. The acceptance test is the operator's own sentence, and it is worth
keeping verbatim because every dimension below is an attempt to answer *it*, not a
checklist invented here:

> give the fleet a single github issue and have it complete e2e and ensure the
> quality is as good as if it was from this exact terminal — that is the test

Run on 2026-09-15 against `master` = `d455fda`, the answer was **FAIL**: the lane
(`ao-796-b9cfeb6a`) ended with 0 commits, 0 changed files, no PR and the issue still
open. The verdict, though, was reached by an **operator reading logs** — grepping
`.fleet/sister.log` by hand and counting `executing directive` **13** times. That is
the finding behind this document: a verdict only a human can produce is not an
instrument. It cannot be re-run on the next lane, it cannot be run *by* the lane,
and its dimensions are whatever the reader happened to think of.

## What is here

| Path | What it is |
|---|---|
| `scripts/check-fleet-parity.sh` | the gate of record's entry point; auto-discovered by `scripts/discover-checks.sh` (#698), so `make verify` runs it with no hand-edit |
| `scripts/fleet-parity/judge.py` | the instrument: `collect` (impure) then `judge` (pure), plus the probe suite |
| `scripts/fleet-parity/plants/healthy.json` | the **negative control**: a subject on which every dimension must be OK |
| `scripts/fleet-parity/plants/plants.json` | one plant per dimension, each a one-field mutation of the healthy subject |

```bash
bash scripts/check-fleet-parity.sh                 # the probe suite (what make verify runs)
bash scripts/check-fleet-parity.sh --issue <n>     # assess a real lane, per dimension
bash scripts/check-fleet-parity.sh --issue <n> --collect-only   # the subject, as JSON
bash scripts/check-fleet-parity.sh --subject-file <path>        # judge a recorded subject, offline
```

Exit codes are the repo tri-state: **0** every dimension measured and holding,
**1** a measured violation, **2** CANNOT-ASSESS. Inside `make verify` an exit of 2
is recorded as a SKIP — *named*, never hidden — which is why a lane that is simply
incomplete cannot fail this check, and why FAIL is reserved for a violation that was
actually measured.

## The shape that makes it provokable

`collect` is the only impure half (git, `gh`, the repo's own checkers). It builds a
**subject**: a plain dictionary. `judge` is pure — subject in, one verdict per
dimension out — and that split is what makes "every dimension is provoked" a
*measurement* rather than a claim. The probe suite plants a subject that moves any
dimension, including the ones that normally need a network, and asserts the
judgement moves.

Two rules are structural rather than conventional:

* a `Verdict` with status OK and **no evidence cannot be constructed** — it raises
  `Unmeasured`. "No claim stated without its measurement" is not a convention this
  instrument follows; it is a shape it cannot express;
* a CANNOT-ASSESS verdict **must name the input that could not be read**. "An
  unreadable input is not a pass" is the same rule from the other side.

## The ten dimensions

| # | Dimension | Measured from | OK means | FAIL means |
|---|---|---|---|---|
| 1 | `verify-output` | the issue's `## Verify` section, and the lane's recorded gate output | a **composite** verdict line is quoted (`verify: PASS…`, `GATE: PASS`, or the attestation projection) | the composite verdict says FAIL |
| 2 | `attestation-sha` | the lane's `.verify/attestation.json` and the merged PR | the attested `git_sha` **is** the merged commit, or shares its tree (squash merge) | the evidence covers a different tree than what merged |
| 3 | `gate-wired` | the PR's file list vs `discover_check_scripts` and the denylist | every `scripts/check-*.sh` the lane delivered is discovered and enabled | delivered but undiscovered, or disabled **by name** |
| 4 | `provoked` | `probe_suite()` run on this machine | all ten dimensions have planted subjects that move them | a dimension is unfalsifiable |
| 5 | `commit-trailer` | the lane's commit message | `Refs kushin77/agent-orchestrator#<n>` is in the **trailing paragraph** | it is in the subject line, or in a body paragraph |
| 6 | `pr-contract` | `scripts/check-pr-contract.sh --pr <n>` | rc 0 | rc 1, quoting the rule the contract names |
| 7 | `isolation-audit` | `governance/isolation/cli.py audit --session <id>` | rc 0 **scoped to the subject's session** | the subject's own session violates the isolation contract |
| 8 | `branch-pushed-then-deleted` | the PR head ref and `git/ref/heads/<ref>` | the head commit is readable back from the remote (so it was pushed), and no remote ref remains | never pushed (AO-GR-23), or the branch still exists |
| 9 | `issue-closed-with-evidence` | the issue state and its comments | CLOSED, and a closing comment carries a command **and** a verdict | still open, or closed with an assertion instead of evidence |
| 10 | `measurement` | the other nine verdicts | every OK verdict carries evidence; every CANNOT-ASSESS names its unreadable input; the grid is complete | a dimension is missing, or a claim has no measurement |

### Two dimensions that refuse to be over-claimed

**`isolation-audit` is scoped, or it is not assessed.** An unscoped
`governance/isolation/cli.py audit` reports every lane in the fleet — measured on
this box while writing this: **85** lane records, of which several fail
(`worktree-missing`, `branch-mismatch`) for reasons that belong to *other* lanes.
Charging those to the subject would be a false-positive generator, so the audit is
run with `--session <id>` and the session is resolved from the registry
(`governance/isolation/cli.py list`). When the registry has no record of the subject
(the lane was closed and pruned), the dimension is CANNOT-ASSESS and the report says
so, in the tool's own words.

**`verify-output` quotes a COMPOSITE verdict, never a per-check line.** The first
version of this instrument reported `shell-syntax: OK (162 file(s))` as "the issue's
Verify: output" — technically a verdict, practically a misquote. Measured on #649,
the post-merge lane's log ends before the composite line (the run was interrupted),
so the composite verdict is taken from the gate's own attestation, and the quoted
line **names its source** (`attestation(<path>): result=PASS checks=130 skipped=4`)
rather than being synthesised. A log with per-check lines only is CANNOT-ASSESS, not
FAIL: from here, a run still in flight and a run that was killed are
indistinguishable, and neither is a measured violation.

## How every dimension is provoked

`plants.json` holds the plants; each is a **one-field mutation** of `healthy.json`,
so the proof names exactly which field the dimension depends on. The suite asserts
both halves — the mutation must have **landed** (the value really changed), and the
same dimension must still be **OK on the healthy subject** — because a judge that
failed everything would otherwise satisfy every failing plant.

| Dimension | Plant(s) | The mutation | Expected |
|---|---|---|---|
| `verify-output` | three | `verify_output` removed · replaced with a prose claim · replaced with `verify: FAIL …` | CANNOT · CANNOT · FAIL |
| `attestation-sha` | two | attestation removed · attested sha with `tree_match: false` | CANNOT · FAIL |
| `gate-wired` | two | the delivered check is not in the discovered set · the delivered check is denylisted | FAIL · FAIL |
| `provoked` | one | a dimension's coverage count set to 0 | FAIL |
| `commit-trailer` | two | the reference moved into the subject line · into a body paragraph | FAIL · FAIL |
| `pr-contract` | one | rc 1 with the contract naming `pr-body-unreproduced-pre-existing-red` | FAIL |
| `isolation-audit` | one | rc 1 with the audit naming `branch-mismatch` | FAIL |
| `branch-pushed-then-deleted` | two | `pushed: false` · `remote_exists: true` | FAIL · FAIL |
| `issue-closed-with-evidence` | two | the issue is OPEN · closed with "Done! Everything checks out." | FAIL · FAIL |
| `measurement` | one probe | constructing `Verdict(code, OK, "")` must raise `Unmeasured` | the rule fires |

The last row is worth naming plainly: the meta-rule cannot be provoked by a
*subject*, because it is a property of the verdict shape, so its probe asserts the
shape instead — an OK verdict with no evidence must be **impossible to construct**.
That probe is why `coverage["measurement"]` is 1.

## A real verdict, quoted

Run against **#649** (merged as PR #851, issue closed 2026-09-16), whose work is
landed and whose lane worktrees are still on this box — i.e. a completed lane the
instrument had no part in producing:

```
$ bash scripts/check-fleet-parity.sh --issue 649
  (subject: lane=/home/akushnir/ao-worktrees/ao-649-postmerge [its attestation IS the merged commit 6eafa4c30e61] attestation=read
            pr: PR #851: head branch issue-649-erp-ops names the issue (rule 15)
            isolation session: not in the registry)
== fleet-parity: issue #649 — 10 dimension(s) ==
  OK             verify-output                `python3 -m pytest integrations/erp/ops/tests -q && make verify` -> 'attestation(.../.verify/attestation.json): result=PASS checks=130 skipped=4'
  OK             attestation-sha              attestation git_sha 6eafa4c30e61 IS the merged commit
  OK             gate-wired                   delivered and discovered: ['erp-ops'] (123 discovered in all)
  OK             provoked                     16 planted subject(s) exercised all 10 dimensions
  OK             commit-trailer               the trailing paragraph of 8c7924fe36f5 carries 'Refs kushin77/agent-orchestrator#649'
  OK             pr-contract                  `scripts/check-pr-contract.sh` -> rc 0; 'check-pr-contract: OK — trailer block, AI-assistance, Closes and the pre-existin'
  CANNOT-ASSESS  isolation-audit              `governance/isolation/cli.py audit` produced no exit code: cannot scope the audit: no lane record names #649 or its worktree, and the audit's other findings belong to 85 unrelated lane(s)
  OK             branch-pushed-then-deleted   issue-649-erp-ops was pushed and is deleted on the remote (no ref remains)
  OK             issue-closed-with-evidence   closed, and a closing comment carries evidence: '## Landed and verified on `master`…'
  OK             measurement                  8 of 9 dimension(s) measured and holding; 1 CANNOT-ASSESS, each naming the input that could not be read

fleet-parity: CANNOT-ASSESS — no verdict may be claimed: isolation-audit
```

The aggregate is CANNOT-ASSESS, and that is the honest answer: eight dimensions
hold, one could not be read. It is **not** a PASS, because the rule this document
exists to enforce is that an unreadable input is not a pass.

The same instrument, pointed at **the lane that built it** while that lane was still
unmerged, reports 2 OK and 8 CANNOT-ASSESS and exits 2 — no FAIL. That is the
behaviour `make verify` depends on: the check runs inside every lane's composite
gate, so a lane that is simply incomplete must be a SKIP (exit 2 is recorded as one,
by name), never a red.

The subject line is printed with every verdict, and it names **how the subject was
chosen** — which pull request (and on what basis), which lane worktree (and why that
one), which isolation session. Two of those were bugs before they were features,
found by running the instrument rather than by reading it: the collector scored
#649 against the *first cross-referenced PR* (the timeline lists eight, seven
belonging to other lanes) and against the *first lane directory* (the pre-merge
lane, whose attested tree is not the squash-merge tree). Both are now
measured selections whose basis is printed.

## What is deliberately NOT here

* **The drain-path refusal classification.** "A refusal must be a state transition
  on the directive, not a no-op that leaves it drainable" is issue **#861**, and it
  is a different lane's surface. This document scores the *outcome* (no work
  product, an unbounded loop) and does not implement the bound.
* **The brain-side live-claim guard.** Same reasoning: another lane's scope.
* **Anything that needs a live fleet.** A gate of record cannot require a running
  fleet; the network-bearing half runs only when an operator names an issue.

## The `drop` remedy (item 3 of the issue)

`python3 fleet/control.py drop --directive <id> --reason <text>` used to answer *"the
sister will dead-letter directive …"* — a message the loop must **process**, so the
remedy for a loop that cannot drain its mailbox had to travel through the mailbox
that loop was not draining. The route is now chosen by **where the order is**, and
the one it can act on, it acts on:

* **in the inbox** → the verb retires it itself through the same single
  implementation the automatic path uses (`runaway.dead_letter`, #754): the order
  leaves the inbox, the terminal artifact is stamped, and `dispatchable()` is False.
  **It is effective with no loop running at all** — which is the whole point;
* **already dead-lettered** → reported, and the artifact is not rewritten, so a
  second drop cannot degrade the first one's evidence;
* **a brain-minted authorisation in `.fleet/sent/`** → relayed over the channel and
  said so, because `governance/lifecycle/directive.py` is the single owner of that
  record and nothing else may move it;
* **nowhere** → refused **by name**.

The **#821 invariant is preserved**: a drop cannot retire an order whose change has
already landed. Landed work is finished, not dead, and dead-lettering it would
record a failure for work that succeeded while masking the authorisation path's own
terminal move. Two hermetic sources answer the question — the directive already
consumed into `.fleet/done/`, and the issue CLOSED in the committed board snapshot —
and an **undecidable subject is refused** (exit 2, CANNOT-ASSESS) rather than
defaulted to allowed. The mailbox contract is intact: one writer
(`runaway.dead_letter`), one record shape (`runaway.RECORD_FIELDS`), and only
`dropped_by` distinguishes the callers.

The one case where the channel is still the route is the `.fleet/sent/`
authorisation — and the reason the inbox case is *not* relayed is written into the
code: `dead_letter` reads the envelope from the inbox, so a relayed second retire
would overwrite the artifact's `envelope` with `null` and destroy the evidence of
what the order carried. The mailbox is the interface, not the message.

`fleet/tests/test_control_drop.py` proves it, with the negative control inside each
assertion: the order is asserted **live and dispatchable before** the drop (or "it
is terminal afterwards" would pass on a verb that retires everything); the relay is
armed to **fail loudly** and the drop still retires; a landed order is asserted
**still in the inbox** after the refusal; and the record shape is compared
field-for-field with the guard's own.
