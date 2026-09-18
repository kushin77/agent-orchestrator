<!--
The merge contract for this repo (AGENTS.md golden rule 1, docs/GOVERNANCE.md
"AI-assistance declaration", docs/EXECUTION-PLAN.md dispatch contract). Fill in
every section; the headings are the machine-readable shape
`scripts/check-pr-contract.sh` looks for.

BEFORE OPENING
  1. Run the issue's own `Verify:` command, then `make verify`, and paste the
     REAL output into Evidence. The last line is `verify: PASS (n of m checks)`
     or `verify: FAIL (k of m checks failed)` — quote it.
  2. Every commit you author carries the ticket trailer on its own line in the
     trailing trailer block (a mention in the subject line is NOT a trailer):

         Refs kushin77/agent-orchestrator#<n>

  3. If a gate is red for a reason this PR does not cause, prove it: reproduce
     it on a clean checkout of the base commit and quote the command and its
     output in "Pre-existing red". An unproven claim of a pre-existing failure
     is treated as this PR's failure (#236 asserted one; #267/#268 reproduced
     theirs — the standard to aim at).

  4. If this PR closes a child issue of an epic, the issue body carries
     `Parent: #<epic>` so the dispatch chain can resolve the child.
-->

## Closes

Closes #<n>

## What changed

<The substance: what this changes and why, in a paragraph or two. Not a diff.>

## Merge order

<!--
Declare whether this PR's diff touches a gate script (a merge-order-sensitive
change should land LAST so it does not re-judge PRs queued ahead of it). The
path list `scripts/check-pr-contract.sh` cross-checks this declaration against
is `scripts/lib/gate-paths.txt`. Pick exactly one line, replacing the
`<paths>` placeholder with the touched gate path(s) when the answer is `yes`:

    Gate-changing: no
    Gate-changing: yes — scripts/check-pr-contract.sh

A declared `no` whose diff touches a gate path, or a declared `yes` whose diff
touches none, is refused by name.
-->

Gate-changing: <no | yes — paths>

## Evidence

```
$ <the exact commands you ran, in order>
<their real output, pasted — including any failure>
```

## AI-assistance

AI-assistance: <runtime> (<model>/<mode>)

## Clock invariant

<!--
The #506 date bomb: a fixture seeded with a literal date while the code under
test resolves its evaluation bucket from the live clock — green on the day it
was written, red every day after (RCA-0008, issue #506). A detector that reads
the fixture cannot stand in for this answer: at byte-identical seed lines it
refuses the defect and its own repair alike. Answer on one line, and write `no`
when this diff pins no date at all.
-->

Clock-invariant: <no time-pinned fixture — or: pinned <what>, with the evaluation bucket resolved from the entity under test and never from the live clock>

## Pre-existing red

None — no failing gate is claimed to be pre-existing.

<!--
Replace the line above ONLY when a gate fails for a reason this PR does not
cause, and then quote a reproduction on a clean checkout of the base commit:

## Pre-existing red

Reproduce: `<the exact command>`

```
<its real output, pasted>
```

A reproduction is what makes the claim evidence rather than assertion; see
[docs/GOVERNANCE.md](../docs/GOVERNANCE.md) for the merge contract and
[docs/QA-GATE.md](../docs/QA-GATE.md) for the gates.
-->

<!--
issue #1001, declared as platform policy by issue #1138
(governance/platform/repo-settings.yaml, `squash_merge_commit_message: PR_BODY`,
`squash_merge_commit_title: PR_TITLE` -- verify with
`bash scripts/repo-settings.sh verify`): the squash commit message on `master`
IS this PR's body ("<title> (#N)\n\n<body>"), never the branch's own commit
trailers. That means THIS is the only place the trailer paragraph can
still be lost before it lands. Leave the line below as the body's FINAL
paragraph (replace `<n>` with this PR's own issue number; do not add anything
after it) so the composed squash message keeps the ticket reference inside a
trailing trailer block. Verify before merging with:

    bash scripts/check-squash-message.sh --pr <this PR's number>
-->

Refs kushin77/agent-orchestrator#<n>
