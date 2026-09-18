# End-to-end GitHub lifecycle — every artifact terminal

> **Status:** institutional · **Issue:** #269 · **Gate:**
> [`scripts/check-github-lifecycle.sh`](../../scripts/check-github-lifecycle.sh)
> (`make verify`) · **Rule:** `AGENTS.md` golden rule 16

[`governance/isolation`](../isolation/README.md) guarantees a lane *opens*
correctly. This package covers the other half: the **close**. A work item is not
done when its pull request merges — it is done when every artifact it created has
reached a terminal state, and the machine can prove it.

The gap was measured, not imagined. Closing the isolation lane (#263) drifted in
five separate ways and no check noticed any of them:

| Drift | What actually happened |
|---|---|
| Branch survived | `gh pr merge --squash --delete-branch` printed `fatal: 'master' is already used by worktree at ...`; the merge landed and the branch stayed. |
| Claim wedged | The fleet's own loop re-claimed the closed issue; the claim then had to be reaped by hand. |
| Directive left sent | The authorisation directive stayed pending, so the order was re-executed after the work was already merged. |
| Lane left behind | A provisioned worktree remained for an issue that was closed. |
| Labels never declared | Refreshing the board exposed open milestoned issues carrying no `class:` label. |

Each was a step in a process that nobody owned.

## 1. The lifecycle

```
filed -> claimed -> laned -> opened -> verified -> merged -> closed -> reclaimed
```

`reclaimed` is terminal. `governance/lifecycle/cli.py status --issue <n>` prints
where an item sits, derived from its artifacts rather than from a field somebody
typed.

## 2. The closure invariants

The vocabulary is **closed**, and every entry carries the requirement it enforces
and the remediation that clears it — so a finding can always say how to fix it,
and the gate can require that each one has been provoked.

| Invariant | Broken means |
|---|---|
| `PR_NOT_MERGED` | A verified change that never landed. |
| `VERIFY_EVIDENCE_MISSING` | "Green" is a claim. Evidence must name a commit whose tree is **the tree that landed** — for an ordinary item the pull request's head commit, and for a branch that advanced after the squash the commit the squash landed as, with the drifted head recorded beside it (§3.7, #1149). Demanding that evidence name the merge commit would fail every correctly-merged item, because a squash merge creates a *new* commit. A lane may therefore stand for that subject only when it **is** it, or — for a *merged* pull request — contains the commit the squash landed as **and** that landing carries the verified work (§3.6, #1098/#1298). When the attestation *also* records the tree it measured (`.verify.measured`), that tree must be one the item's own record carries: the head commit's own tree, or the merged tree the squash composed (§3.6, #1003). A record that does not say which tree it measured is read as before. |
| `BRANCH_NOT_DELETED` | The branch outlived its issue. |
| `CLAIM_STILL_HELD` | A closed issue still claims a lane, blocking re-dispatch. |
| `DIRECTIVE_NOT_CONSUMED` | A pending directive re-executes the order the moment the claim frees. |
| `LANE_NOT_RECLAIMED` | Stale lanes accumulate and collide. |
| `CLOSING_EVIDENCE_MISSING` | A summary is not evidence. |
| `ISSUE_NOT_CLOSED` | The change landed but the item is still on the board. Closing the issue *is* a closure step, which is why applicability is keyed on whether the change landed, never on the issue already being closed. |
| `CHILD_NOT_CLOSED` | An epic was closed while a declared child is still open. "Declared" is mechanical — a child whose body's first line is `Parent: #<n>` naming the epic, the same marker `dispatch/snapshot.py` parses into `Issue.parent` — never a hand list. (Subject: an epic's child set, not an item.) |
| `EPIC_CHILD_MARKER_MISSING` | An epic is checked against a child whose edge to it **cannot be established** — a supplied child declaring no `Parent: #<n>` marker at all — so it can be shown neither closed nor open. `CHILD_NOT_CLOSED` can only fail on children the audit managed to tie to the epic, so without this rule "no children found" would read as "every child is terminal" and an epic could close over a child the audit never saw. A child naming a *different* parent is decidable (it belongs to that parent) and is therefore neither declared nor reported. (Subject: an epic's child set, not an item.) |
| `FILING_LABELS_MISSING` | An open, milestoned item declares no `class:` label, so the conformance gate cannot hold it to a rung. |
| `QUARANTINE_STALE` | A legacy excuse outlived the issue tracking it. (Subject: the baseline, not an item.) |

## 3. Close-out

`python3 governance/lifecycle/cli.py close --issue <n>` drives the invariants in
dependency order. The order is derived from the incident above, not from taste:

1. **merge** — the verified head is what lands;
2. **record verification** for that head commit;
3. **delete the source branch** — which a local squash-merge reliably leaves behind;
4. **consume the directive** — *before* releasing the claim, because on #263 the
   still-pending directive was re-executed the moment the claim freed (§3.1);
5. **release the claim**;
6. **close the issue with evidence** — which only exists once 1–2 have run;
7. **reclaim the lane last** — so a failure earlier leaves the worktree available
   for the re-run that finishes the job.

Every step is idempotent (it asks the operations port for current state and skips
when the invariant already holds), and the executor **re-collects the item through
the operations port and re-derives the findings afterwards**: `ok` is the absence
of findings in the *fresh* state, never the absence of exceptions. Auditing the
pre-close item would report a fully successful close as NOT-OK — the inverse of
the false green this module exists to prevent, and a bug the third end-to-end run
caught.

### 3.1 The directive's terminal move

Step 4 is owned **here**, not delegated to a mailbox CLI. A brain-minted
authorisation is written to `.fleet/sent/<id>.json` (the chain edge
`claim --directive <id>` validates, golden rule 14); *consumed* means the record has
reached `.fleet/done/`. Until #821 the close-out shelled out to
`fleet/channel.py consume`, which reads only the **inbox** — a mailbox a brain
directive never enters — so it answered `nothing to consume` (rc 1) for the very
artifact the finding named, and every brain-dispatched lane ended in an
undocumented hand-move. Measured on this box: 118 stranded directives in
`.fleet/sent/`, none of them retireable by any documented command.

[`directive.py`](directive.py) is now the single owner of the `sent` → `done`
transition, and its gate is a fact the close-out already holds:

| Condition | Decision |
|---|---|
| no record carries the id | refuse by name |
| the record names no issue | refuse — an authorisation whose subject cannot be established may not be retired |
| the issue is outside the lifecycle record | refuse — absence is not permission |
| the ordered change has **not landed** | refuse; the record stays in `sent/`, so live work cannot be retired |
| the ordered change has landed | move the whole stranded set for that issue into `done/` |
| a byte-identical file is already in `done/` | remove the duplicate; a *differing* file is refused, never overwritten |

"Landed" is the model's own `owes_closure` (the issue is closed, or its pull
request is merged), reused rather than restated so the gate and the closure
invariants cannot drift. It is deliberately **not** "the issue is closed":
close-out consumes at step 4 and closes the issue at step 7, so that criterion
would leave the documented path unsatisfiable for every ordered lane — the defect
class this change removes. A live order is refused by name and stays `sent`.

The whole stranded set for the issue moves in one pass, because the invariant is
about the *order* being retired; moving one and leaving a sibling pending would
reproduce the same finding while `sent/` kept growing. Every collision is checked
before the first move, so a refusal never leaves the mailboxes half-moved.

### 3.2 The third verdict: CANNOT-ASSESS

`close --issue <n>` has three outcomes, and the exit code carries which:

| Verdict | rc | Means |
|---|---|---|
| `OK` | 0 | every invariant holds in the *fresh* state |
| `NOT-OK` | 1 | a named invariant was measured broken |
| `CANNOT-ASSESS` | 2 | nothing is known broken, and something could not be measured |

The third exists because `scripts/verify.sh` is no longer the only thing that decides
what a non-zero exit code means. Since the admission control (#724) a gate that
cannot take a permit exits with a code of its own, deliberately **outside** the
gate's 0/1/2 tri-state: `10` another gate holds this worktree, `11` every box-wide
permit slot is taken (`AO_GATE_MAX_CONCURRENT`), `12` the permit store is unusable.
All of them mean *nothing ran and no attestation was touched*.

**Measured: `make verify` exits 2, not 11.** GNU make reports a recipe's failure and
exits 2 for *any* failing recipe — `make: *** [Makefile:146: verify] Error 11` is
make's *message*, not its status — so a park, an unusable permit store, the gate's own
CANNOT-ASSESS and a failing check all arrive at the consumer as the same number. The
distinction lives in the gate's **own final verdict line**, and that is what
[`gate.py`](gate.py) reads:

```
$ make verify ; echo $?
verify: PARKED (rc 11, not a pass and not a failure) — the box-wide gate cap is reached; nothing was run and no attestation was touched
make: *** [Makefile:146: verify] Error 11
2
```

It reads the **last** such line, because every check's output is teed into the same
stream and a check may provoke a refusal (printing one) to prove the control works;
ordering is preserved by folding stderr into stdout in the child, so the gate's own
banner is genuinely last. Reading only the exit code was the first form of this fix,
and a real park (real cap, real lane) showed it mislabelling the case as an unusable
permit store — the fixture in the tests now reproduces make's own behaviour, exit code
and `Error N` line included.

Measured on #836: step 2 read rc 11 as "no green attestation", so a capacity
condition was reported as `VERIFY_EVIDENCE_MISSING` — an assertion that evidence was
measured and came back absent — and the remediation printed with it ("run `make
verify` on the branch head") re-parked on every attempt, because the cap was still
full. [`gate.py`](gate.py) owns the fix, and its table is closed:

| What the gate declares | Verdict | Why |
|---|---|---|
| `verify: PASS` (rc 0) | `admitted` | the gate ran and passed |
| `verify: FAIL` (rc 1) | `failed` | **a check ran and disagreed — the only meaning that is a failure** |
| `verify: PARKED (rc 10, 11)` | `parked` | refused a permit; nothing was run |
| `verify: CANNOT-ASSESS (rc 2)`, or rc 12 | `unassessed` | it could not measure, or its permit store is unusable |
| no verdict line, rc 128 + N | `unassessed` | killed by signal N (SIGTERM 143, SIGINT 130) |
| no verdict line, anything else | `unassessed` | it reported none of its own outcomes |

The process exit code is the **fallback** for a caller that propagates the gate's own
code; under `make` it is 2 for every non-pass, which is why the transcript decides.

The admission codes are **imported from their owner** (`fleet/gatelock.py`), never
restated, so a change on the producer's side cannot silently re-label the consumer's
verdict. A parked or unassessed run:

- ends its step as `parked`/`unassessed` — a distinct outcome, not `failed` — and the
  close-out carries it in `not_assessed`;
- retires only the **unmeasured** `VERIFY_EVIDENCE_MISSING` finding; an attestation
  that is recorded and names the wrong commit is a measured mismatch and stays a
  finding, and an item with no verified head commit is broken for a reason no gate
  run can fix;
- files **nothing** on the board, because a defect report generated by capacity is a
  false report;
- is **never** a pass: the verdict is `CANNOT-ASSESS` and the exit code is 2.

The bounded retry waits for a permit to free (`AO_LIFECYCLE_GATE_RETRIES`, default one
attempt after the first; `AO_LIFECYCLE_GATE_RETRY_WAIT`, default 30s; `RETRIES=0`
disables it) and **records every attempt** in the step it reports, so a retry is never
mistaken for a single pass or a single failure. Only a park is retried — rc 1 is an
answer, and re-running it would ask the same question twice.

A run that did not happen writes **no journal**. `.fleet/lifecycle/<issue>.json`'s
*presence* is `governance/reconcile`'s landing record, so journalling a park there
would let capacity be read as a landing — the substitution golden rule 17 forbids.

### 3.3 Resolving the lane, and clearing the records beside it (issue #834)

Step 2 needs a tree, and an issue can hold **more than one** lane record. That is
not hypothetical: `.fleet/lanes/` held 80 records for 79 issues on this box, and
**30 of them have no worktree** — a reaper removed the tree and the record stayed.
`_lane_records` used to collapse them by issue with the later-sorted record
winning, so such a dead record **shadowed the live lane** and step 2 refused:

```
  failed    record-verification: RuntimeError: no lane worktree for #287; the verified tree no longer exists
```

while the live lane existed, audited clean, and held the verified commit. The
record set is now kept whole and the choice is explicit:

| Preference | Why |
|---|---|
| a record whose **worktree exists** | a dead record may never shadow a live lane |
| then the record whose **HEAD is the verified commit** | that is the tree the evidence is about |
| a dead record, if it is all there is | so the refusal can say `worktree-missing: <path>` — a reaper removed the tree — instead of "no lane worktree", which sends the operator hunting for a tree the lane provisioned itself |

A dead record is also a **finding**, not nothing: `LANE_NOT_RECLAIMED`'s requirement
is that the worktree *and the record* be gone, and the detail carries the isolation
audit's own vocabulary (`worktree-missing`). Treating it as nothing is what let it
shadow a live lane in the first place. Because the invariant is still owed, step 8
reclaims the dead siblings beside the live lane in the same pass — a sibling whose
worktree is gone holds no work, so retiring its record discards nothing, and the
item can still reach `OK` rather than being reported forever.

### 3.4 Reclaiming a tree the close-out dirtied itself (issue #834)

`closer.reclaim_lane` refuses a dirty worktree, and the close-out's **own step 2**
makes the lane dirty: `make verify` runs `fleet/tests/test_brain.py`, which drives
the real brain loop whose `advance_epic_focus()` rewrites the checkout's
`.board/focus.json` (measured 2026-09-15: `active_epic` `707` → `160`, sha
`397bed81c9f6` → `171fbf96eaff`). Step 8 then refused the tree step 2 had just
written to, so the close-out was racy against the fleet that owns the file — and
against itself.

The dirty test now distinguishes **the lane's own work** from state a *machine*
rewrites: `governance/isolation/worktree.py` declares `MACHINE_MANAGED_PATHS`
(`.board/focus.json`, and nothing else), refuses only on the lane's own paths —
**naming them** — and the reclaim **reports what it ignored**, so "the tree was
clean" and "the tree was dirty only in state the fleet regenerates" are
distinguishable in the output. Fixing that exposed a second defect in the same
function: `force=True` skipped only the module's own check and never reached
`git worktree remove`, whose dirty test then refused anyway — so the `--force` the
refusal told the operator to pass did not work.

The gate is [`scripts/check-lifecycle-reclaim.sh`](../../scripts/check-lifecycle-reclaim.sh):
all three wedges are provoked against a real repository, and each fix is reverted
in a mutant that must make the controls go red.

### 3.5 Step 7 is gated on step 2, and the record survives the lane (#786)

Step 2 — `record-verification` — **measures the lane worktree**, and step 7 — `reclaim-lane`
— **removes it**, so the order between them is load-bearing *and irreversible*. Until #786
the driver only *ordered* them: `reclaim-lane` ran whether or not `record-verification` had
recorded anything. A transient PARK — the
by-design outcome of the admission control while four other lanes are gating — or a
genuine failure therefore destroyed the only tree the evidence could have come from,
and the invariant naming that evidence became **permanently unsatisfiable**. Measured
three times in one dispatch on the lanes of epic #616 (#622, #623, #626) and again on
#793 (#854):

```
  failed    record-verification: RuntimeError: no lane worktree for #622; the verified tree no longer exists
  REMAINS  VERIFY_EVIDENCE_MISSING  #622  no green verification attestation is recorded
```

A permanently red record for work that *was* verified, carrying a remediation ("run
`make verify` on the branch head") that no longer had a branch to run it on. A control
that cannot succeed is a formality; this one **inverted**. The order is therefore
enforced from both ends:

| End | Mechanism |
|---|---|
| **the driver** refuses the unsafe order | `reclaim-lane` is **withheld** while the item still owes `record-verification`, refused by name, and the lane is kept. The next pass finishes the job — the retry the eight-step design always assumed, made reachable instead of asserted. |
| **the record** is order-independent | `record-verification` re-measures the **verified head commit** once the lane is gone, in a throwaway detached worktree, and removes that tree again. The commit — not the branch, not the worktree — is what proves the tree. |

Three consequences worth stating, because each is a place a change could quietly
weaken this:

- a lane kept **on purpose** is not reported as `LANE_NOT_RECLAIMED`. That finding's
  remediation ("close the lane, committing or discarding its work first") **is** the
  wedge in this state, so a driver that printed it would be instructing the operator
  to destroy the evidence. The withholding is printed as a step and named in
  `WITHHELD`, and the finding that remains is the root cause. A lane the driver
  *tried* to reclaim and could not is still charged in full;
- withheld is **not** unassessed. A park still reports CANNOT-ASSESS and never NOT-OK
  (#840): nothing was measured, and keeping the lane is what makes the next attempt
  able to measure it;
- re-measurement is not a blanket pass. A red gate at that commit stays a failure, and
  a commit the repository does not hold is refused **by name**, naming the ordering
  that would have prevented it — never the old dead end.

The controls live in
[`scripts/check-lifecycle-verify-order.sh`](../../scripts/check-lifecycle-verify-order.sh),
which drives the real driver, the real port and the real reclaim command against a real
repository whose lane is a real `git worktree`: a lane reclaimed before close-out whose
gate was **green** has its invariant *satisfied*; the same lane with a **red** gate, or
an unreachable commit, is still refused by name; and disabling either half of the fix
reproduces the wedge, so neither half is decoration.

### 3.6 A squash merge decides which commit a lane can be measured against (#1098)

§3.5 names the commit as what proves the tree. That is right, and it was **half** the
problem: for a **squash-merged** pull request the commit that was verified — the branch
tip — is not an ancestor of anything on the default branch. The merge created a *new*
commit carrying the same tree, so a lane cut from the default branch (the correct venue,
rule 15) can never be **at** the verified head, and `record-verification` refused it:

```
failed    record-verification: RuntimeError: lane head 96ae0fba19c3 is not the verified commit a06badc9eb80
```

Measured on #714, #977 and #978 — all three merged, none closable, all three left
carrying `VERIFY_EVIDENCE_MISSING`. The obvious remedy (reset the lane to the branch tip)
satisfied the equality and then measured an **obsolete tree**: the composite gate is not
tree-local, so its repo-wide invariants failed for reasons unrelated to the change —
measured at PR #984's head, **19 of 145 checks failed**, for a tree no green attestation
had ever existed for.

The rule is therefore widened in exactly one direction, and the second arm needs **both**
of its halves:

| A lane may stand for the verified commit when | |
|---|---|
| it **is** the verified commit | unchanged — the equality arm, and the only arm for an item that has not merged |
| it **contains the landing**, *and* the landing carries the **verified work** | the squash arm. `landing` is the commit the merge landed as, offered only when the pull request is genuinely merged |

The second half is what keeps the doctrine intact — "the tree which was verified is the
tree that landed" — so a lane containing a landing built from *other* content is refused
rather than measured as if it proved this item. It is asked as a **change**, not as
whole-tree equality (#1298). A squash merge composes its landing from the base **at merge
time**, so the moment anything else lands on the default branch between the branch cut and
the merge the landing carries content the branch tip never had and the whole trees differ
*by construction*: equality was satisfiable only while nothing else landed, so the arm that
exists for a squash-merged item could not fire for one whose base moved — a control that
cannot fire, the inverse of GR-12.

The second half therefore holds when any one of three measured facts does, each of which
answers a shape the others cannot:

| The landing carries the verified work when | |
|---|---|
| the verified commit is **in the landing's history** | the merge-commit form: the landing descends from the very commit that was gated |
| the landing carries the **change the verified commit introduced** | every path the tip changed against its merge base with the landing resolves to the **same blob** there — an addition, a modification and a deletion all carried, with the landing's other content being the sibling landings that moved the base |
| the **whole trees are identical** | the original #1098 case, asked when no path-by-path comparison was possible, because it needs no ancestry at all |

Containment of the landing is still required, and a landing that carries neither is still
refused: containing *a* landing is not the claim, containing *the verified work* is. An
unreadable commit, no common base, or a change nothing could read is `unknown` — never a
pass.

Measured on this repository's own history: branch tip `17dc00a` carries a change that
landed as `870eb26` — the same `git patch-id --stable`, the same twelve paths, identical
content at every one of them — while `git diff --quiet 17dc00a 870eb26` is not clean and
the tip is not an ancestor of the landing. The record then names **all three**
commits, because each answers a different question:

```json
{"verify": {"ok": true, "commit": "<the verified head — what the evidence is against>",
            "landing": "<the commit the squash landed as>",
            "measured": "<the tree the gate actually ran in>",
            "via": "contains", "source": "lane"}}
```

`commit` deliberately keeps naming the **verified** commit: that is the convention the
audit, the invariant's own text, the table above and every pre-existing record use, and
re-pointing it at the measured tree would have silently invalidated each of them. The lane
is the measurement; the verified commit is the subject; the record says which is which.

All of it is provoked in
[`check-lifecycle-verify-order.sh`](../../scripts/check-lifecycle-verify-order.sh) against
a **real** squash merge: a lane that contains the landing is **admitted** and journalled;
a lane containing no landing of the item's is **refused by name**; and removing either the
containment arm or the tree check reproduces the wrong answer, so neither half is
decoration.

The base-moved shape is provoked the same way — a real repository, a real squash merge, a
real lane — in [`tests/test_squash_base_moved.py`](tests/test_squash_base_moved.py): the
base-moved item is **admitted** by the shipping port and journalled, each of the three
clauses is shown by measurement to be the *sole* clause that could have answered its shape,
and every refusal the arms exist for (an unmerged pull request, a lane that does not
contain the landing, a landing that carries neither the verified tree nor the verified
change) is asserted on the ACTUAL line the port printed.

### 3.7 Which commit the evidence is *against* — the tree that landed (#1149)

§3.6 fixed *whether* a lane may stand for the verified commit. It left *which commit the
evidence names* read straight off the live pull request:

```python
verified_commit = str(pr.get("head_commit") or "")
```

For a branch that received commits **after** the squash, that commit is a tree that **never
landed and never gated**. Measured on #977/#978 through PR #984:

| fact | value |
|---|---|
| `pulls/984.head.sha` (live) | `e3f63457cac0…` |
| `pulls/984.merge_commit_sha` | `fed4e7d433e4…` |
| `git diff --quiet <head> <merge>` | **rc 1** — 54 files, 4731 insertions(+), 31 deletions(-) |
| `git merge-base --is-ancestor <head> <merge>` | **rc 1** |

§3.6's rule then refuses the item for the *right* reason (`contains the landing ? True,
landing tree == verified tree ? False`) and the item is **structurally unclosable**: the
invariant is keyed to a tree nobody verified and which never landed, and there is no other
subject to offer. The candidate remedy "resolve to the last PR commit that is an ancestor of
the default branch" is dead on this very case — a squash merge leaves **no** commit of the
branch an ancestor of the default branch.

So the subject is resolved by measurement, once per item, in `closeout.evidence_subject`:

| live `head_commit` vs `merge_commit` | subject |
|---|---|
| trees **same** — the ordinary correctly-merged item | the head commit, unchanged; its tree *is* the landed one, so every existing record and the `clean_item` fixture keep their meaning |
| trees **different** — the branch advanced after the squash | the **landing**, the commit that carries the tree that landed |
| trees **unreadable** | the head commit, unchanged — nothing new is *claimed* about a tree nobody read, and the admissibility rule still refuses any lane that would need it |

The comparison is a **tri-state** (`tree_relation`): "the trees differ" and "the trees cannot
be read" are different facts, and reporting the second as the first would announce a drift
nobody measured. The drift is **disclosed, not hidden** — the attestation carries
`drifted_head`, and the step says the evidence is against the tree that landed:

```json
{"verify": {"ok": true, "commit": "<the landing - the tree that landed>",
            "landing": "<the same commit>", "measured": "<the lane's tree>",
            "drifted_head": "<the live head whose tree never landed>",
            "via": "contains", "source": "lane"}}
```

Nothing is accepted on weaker grounds than before: `_admissible`'s two arms and its tree half
are exactly #1098's. Only the refusal is sharper, because the two causes have different
remedies — a lane of the wrong tree is re-cut, a subject naming a tree that never landed is
re-pointed:

```
lane head <master> is not the verified commit <pr head>: it DOES contain the landing
<merge>, but the landing carries a different tree — <pr head> names a tree that never
landed, so it may not be the subject of this item's evidence; the tree that landed is the
one <merge> carries (#1149)
```

`audit` is widened in the same direction and no further: evidence satisfies the invariant when
it names the head commit, **or** names the landing as its subject **and** records the live head
it drifted from. A record that merely names the merge commit stays a finding — a substitution
nobody measured is still a mismatch.

`check-lifecycle-verify-order.sh` provokes all of it: a real squash merge whose branch
advanced afterwards is closed out **green** on the landed tree, with the journal naming the
landing and the drifted head; the port still **refuses** that drifted subject, in its own
words; and disabling the resolution reproduces the wedge, so the remedy is load-bearing rather
than decoration.

### 3.8 A frozen branch head that can never be made green (#1003)

#786 made *a lane that is gone* recoverable. It left its sibling unsolved: the lane is
still there, and the tree at its head is **permanently red** for a reason the tree that
landed does not contain. Measured on #955, where the close-out refused identically for
**seven attempts over ~2 hours**:

| | |
|---|---|
| lane head `4ed3fc0`, branch `issue-955` | committed **16:18:30**, without the declaration |
| `72dca6c` (issue #959) lands on master | **16:16:48** — before the head was committed |
| squash `494ff91` (PR #964) | composed **16:19:38**, with `72dca6c` as its **parent** |
| `e2e` at the frozen head | **3 failed** — the declaration is absent (`grep -c` → 0) |
| `e2e` at the merged commit | **11 passed** — the landed tree holds it |

A commit is immutable, so *re-running cannot converge*: the invariant was being measured
at a tree that could never be green. The question the invariant actually asks — **did the
change that landed reach a green gate?** — is answered for an already-merged item at the
**merge commit**, and only there: for a squash merge no other commit in the object store
holds the landed tree.

So `record-verification` now has three venues, tried in this order:

| # | Venue | When | Recorded as |
|---|---|---|---|
| 1 | the gate re-run **in the lane** | whenever the lane is there — the tree and the run are the same object, which nothing else can be | `source: lane` |
| 2 | the **verified head commit**, in a throwaway detached tree | the lane is gone (#786) | `source: reclaimed-lane` |
| 3 | the **merge commit**, in a throwaway detached tree | the head was measured **red**, the pull request is **merged**, and the landed tree is a **different commit** | `source: merged-tree`, plus `measured: <the merge commit>` |

The attestation still **names the verified head** — the invariant's subject is unchanged,
and naming the merge commit as the subject would let one stand for a tree nobody verified.
The venue is recorded *beside* it, so the record says what was actually measured:

```json
{"verify": {"ok": true, "commit": "4ed3fc0…", "source": "merged-tree", "measured": "494ff91…"}}
```

Five things bound it, and each one is provoked by a test in
[`tests/test_frozen_head.py`](tests/test_frozen_head.py):

- the head is measured **first**, so the strongest evidence is still preferred and a green
  head is never replaced by a re-measurement;
- the merge commit is the item's **own** pull request's, so it contains the change under
  test — a defect the lane carries is red there too, and the fix cannot launder a red lane;
- it is tried only when the landed tree is a **different commit**, so no tree is gated
  twice and the common case (head and merge are one tree) costs nothing;
- a **park is not a red**: a capacity condition is answered by the bounded retry, never by
  measuring somewhere else, so CANNOT-ASSESS is still CANNOT-ASSESS (#840);
- when the landed tree is red too, the refusal **names both trees** and the invariant
  stands. No journal is written for a red tree, ever.

The clause on `measured` is a **strengthening**, not a relaxation: an attestation that
records a venue the item's own record does not carry is refused where it used to be
believed, and every record written before #1003 — which says nothing about a venue — is
read exactly as it always was.

### 3.9 A red verification names the check it disagreed with (#1247)

A refusal that says *something is wrong* and not *what* cannot be acted on, and until
#1247 the driver could not say what. `scripts/verify.sh`'s own banner names how many
checks failed and which ones were **skipped** — code 2, the tri-state's CANNOT-ASSESS —
and never which ones failed:

```
verify: FAIL (2 of 142 checks failed, 4 skipped: module-registry, module-brief, diagrams-declaration, codeidx-surface)
```

Measured on #627 and #629, whose close-outs refused with exactly that and nothing else
(`.fleet/lifecycle/ledger.jsonl`, 2026-09-18T09:07–09:37), and whose two board findings
(#1247, #1251) therefore carried `1 of 142 checks failed` and `2 of 142 checks failed`
as their whole account of the defect. Finding out *which* check meant running the entire
composite gate again, so the finding stayed unresolved for a reason that was entirely
about the report and not about the code.

The names are already written, by the gate, on every failed run: `.verify/attestation.json`
carries one entry per check — `{"name": …, "rc": …}` — and `scripts/verify.sh` writes it
**even on failure**, deliberately ("so a red run still carries evidence"). Step 2 now reads
it, through the same single reader the park/FAIL/signal table goes through, and:

| | |
|---|---|
| what is named | every check whose `rc` is neither `0` (PASS) nor `2` (SKIP) — the gate's own tri-state, quoted from its producer rather than re-derived from prose |
| where it is named | the refusal's **first** clause, and `RedGate.failing` beside `RedGate.verdict_line` for a composed refusal that has to name two red trees |
| why first | `closeout` clamps a step's detail at 300 characters, and the gate's banner already spends most of that on counts and skips — a name that arrives after the bookkeeping is still unactionable |
| when nothing is named | when the attestation is not demonstrably **this run's**: its `git_sha` must be the measured tree's `HEAD`, and it must have been written at or after the attempt started. A lane keeps an older run's record in the same worktree, and a run that died before writing one must not be described by the previous run's red |

The count is still reported when no name can be read — the driver says what it measured
and never invents a name, which is the substitution this module exists to prevent.

### 3.10 The state root, and why the fixed code needs a seam (#1247)

`ROOT` is this file's own checkout, so a checkout's copy operates on its own state. The
driver has two requirements that a lane worktree cannot satisfy at once:

- it must run the **fixed** code — the remedy from §3.8 is on `master`; and
- it must run against the **fleet's** state — the journals, the lane records and the
  decision ledger the fleet loops write in the shared checkout, without which an item is
  not even in the audit's scope (`audit.in_scope`).

The shared checkout is not a stable code baseline: it holds whichever branch a lane last
left it on. Measured on this box while diagnosing #1247, it sat on
`issue-708-wire-runaway-guard`, **3.7 hours behind `origin/master`**, so its copy of
`cli.py` still refuses a red frozen head outright — the §3.8 remedy could not be
exercised from it at all.

`AO_LIFECYCLE_ROOT=<repo>` therefore points a lane's copy — the one carrying the fix —
at the state that matters, instead of the fleet having to choose which of the two to lose:

```
AO_LIFECYCLE_ROOT=/home/akushnir/agent-orchestrator \
  python3 <lane>/governance/lifecycle/cli.py close --issue 627
```

An override that is not a repository is **refused by name**, not used: it decides where
`.fleet/lifecycle` is written, and that file's *presence* is what `governance/reconcile`
reads as a landing record, so an unvalidated override would scatter landing records
outside the fleet's state — the substitution golden rule 17 forbids, arriving by typo. An
unset or empty override is this checkout, unchanged. Both halves are pinned by
[`tests/test_lifecycle_root.py`](tests/test_lifecycle_root.py).

## 4. Auditing, and why it is offline

`cli.py collect` reaches GitHub; `cli.py audit` never does. The rules are asserted
against fixtures the gate writes, so a gate cannot go green because the record it
reads is stale — the failure mode measured in `.board/snapshot.json` (tracked by
#170). The runtime audit collects live; the *mechanism* is exercised offline.

`audit` states its **scope** in every report, so a narrow audit cannot be mistaken
for a clean board.

## 5. Legacy: quarantined by name, and only while tracked

Pre-existing drift is grandfathered in [`baseline.json`](baseline.json), one entry
per `(code, subject)` pair, each naming the issue that tracks it. An entry is
honoured **only while that tracking issue is still open**; when it closes, the
entry itself becomes a `QUARANTINE_STALE` finding. The quarantine therefore only
shrinks, and a *new* item failing the same invariant fails immediately — an excuse
never spreads from one item to another.

## 6. Tests and enforcement

`governance/lifecycle/tests` (declared in
[`scripts/pytest-suites.txt`](../../scripts/pytest-suites.txt)) pins the
vocabulary, the applicability rule, one provocation per invariant, close-out
ordering and idempotence, and the quarantine's shrink behaviour. Two suites cover
§3.2: `test_gate.py` pins the exit table and the retry policy, and
`test_verify_port.py` drives the *real* port against a stub gate returning 11, 1 and
0 — with a real worktree, a real git head and a real journal write.

`scripts/check-github-lifecycle.sh` runs in `make verify` as `github-lifecycle`:
it provokes one violation per invariant and requires the audit to name it, and it
**cross-checks the provoked set against the model** — adding an invariant without
provoking it fails the gate rather than shipping an unexercised rule.

The execution loop ([`fleet/terminal.py`](../../fleet/terminal.py)) runs close-out
after every dispatch and carries its verdict in the report, so a partial close
reaches the brain instead of being discovered later by hand.

## 7. Declared controls, decision ledger, frozen schema, live feed (issue #885)

Four artifacts make the package's evidence machine-checkable rather than
prose, per the surface-class ladder (`governance/conformance/surfaces.py`):

| Artifact | File | Read by |
|---|---|---|
| **controls** | [`controls.yaml`](controls.yaml) + [`policy.py`](policy.py) | `model.py` (import-time: the closed invariant vocabulary must match `controls.yaml` in both directions — a drift is `PolicyUnavailable`, CANNOT-ASSESS); `directive.py` `retire` (`policy.load_for_model().check_retire` — the reason-length floor and the superseded-by requirement, not a hard-coded check) |
| **audit** (decision ledger) | [`ledger.py`](ledger.py), written to `.fleet/lifecycle/ledger.jsonl` | `directive.consume` and `directive.retire` (exactly one record per call: `ok` or `refused`, schema-validated before it is written); `cli.py` `cmd_close` (one record per close-out verdict) |
| **schema** | [`lifecycle.schema.json`](lifecycle.schema.json) | `ledger.py` (validates every record against `$defs/ledgerRecord` before appending, reusing `governance/modules/schema.py`'s stdlib-only validator rather than a second implementation) |
| **live feed** | [`live.py`](live.py), exposed through the existing verb `cli.py status --live` | an operator or another gate wanting every in-scope item's stage in one call, derived from `model.stage_of` on the SAME record `audit`/`status`/`close` already read — never a second collection |

`controls.yaml` declares two judgments that used to live only in code:

* the **closed vocabulary** — one entry per `model.INVARIANTS` code, so an
  invariant this package can emit that `controls.yaml` does not declare (or
  vice versa) is a policy defect refused at import time, not a rule nobody
  reviewed;
* the **retire preconditions** — `retire.min_reason_length` and
  `retire.require_superseded_by`, read by `directive.py`'s `retire` instead of
  the bare `not reason.strip()` it used to carry, so the threshold is
  reviewable and can be raised without a code change (`AO_LIFECYCLE_CONTROLS`
  overrides which file is read, for the gate's own mutation provocation).

`scripts/check-github-lifecycle.sh` provokes one violation per new artifact —
a control mutated (refused by name), a missing ledger record (refused), a
schema-invalid record (refused), and a live feed that has drifted from the
record it claims to project (refused) — the sha256-restore idiom the rest of
the script already uses.

## 8. Board reporting

A finding must reach the board, not only a log line. `cli.py audit --apply` and
`cli.py close --apply` file a GitHub issue per non-terminal artifact carrying the
named violation and its remediation (`report.py`, issue #321).

- **Idempotent.** One issue per fingerprint (`lifecycle:<code>:<subject>`), deduped
  through `.fleet/board-reports.json`, so repeated passes do not spam the board for
  the same unresolved invariant.
- **Dry-run by default.** No board write happens without `--apply`; a dry-run
  prints `board: dry-run — would file for …`.
- **Offline-testable.** The board effects are an injected port, exercised by
  `governance/lifecycle/tests/test_boardreport.py` with no network.

