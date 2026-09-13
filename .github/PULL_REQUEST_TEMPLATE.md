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
-->

## Closes

Closes #<n>

## What changed

<The substance: what this changes and why, in a paragraph or two. Not a diff.>

## Evidence

```
$ <the exact commands you ran, in order>
<their real output, pasted — including any failure>
```

## AI-assistance

AI-assistance: <runtime> (<model>/<mode>)

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
