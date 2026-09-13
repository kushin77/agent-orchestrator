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
| `FILING_LABELS_MISSING` | An open, milestoned item declares no `class:` label, so the conformance gate cannot hold it to a rung. |
| `QUARANTINE_STALE` | A legacy excuse outlived the issue tracking it. (Subject: the baseline, not an item.) |

## 3. Close-out

`python3 governance/lifecycle/cli.py close --issue <n>` drives the invariants in
dependency order. The order is derived from the incident above, not from taste:

1. **merge** — the verified head is what lands;
2. **record verification** for that head commit;
3. **delete the source branch** — which a local squash-merge reliably leaves behind;
4. **consume the directive** — *before* releasing the claim, because on #263 the
   still-pending directive was re-executed the moment the claim freed;
5. **release the claim**;
6. **close the issue with evidence** — which only exists once 1–2 have run;
7. **reclaim the lane last** — so a failure earlier leaves the worktree available
   for the re-run that finishes the job.

Every step is idempotent (it asks the operations port for current state and skips
when the invariant already holds), and the executor **re-derives the findings
afterwards**: `ok` is the absence of findings, never the absence of exceptions. An
operations port that reports success without acting still fails, and a step that
cannot complete is reported while the remaining steps continue.

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
