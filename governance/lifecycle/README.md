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
| `VERIFY_EVIDENCE_MISSING` | "Green" is a claim. Evidence must name the pull request's **head commit** — a squash merge creates a *new* commit, so demanding that evidence name the merge commit would fail every correctly-merged item. What matters is that the tree which was verified is the tree that landed. |
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
ordering and idempotence, and the quarantine's shrink behaviour.

`scripts/check-github-lifecycle.sh` runs in `make verify` as `github-lifecycle`:
it provokes one violation per invariant and requires the audit to name it, and it
**cross-checks the provoked set against the model** — adding an invariant without
provoking it fails the gate rather than shipping an unexercised rule.

The execution loop ([`fleet/terminal.py`](../../fleet/terminal.py)) runs close-out
after every dispatch and carries its verdict in the report, so a partial close
reaches the brain instead of being discovered later by hand.

## 7. Board reporting

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

