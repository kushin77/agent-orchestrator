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

## Classification

<!--
Issue #1254 step 5 / #1328: the PR's own tag record, judged by
`scripts/check-pr-contract.sh` (WARN-ONLY today; AO_PR_CONTRACT_ENFORCE=1
flips it to a hard refusal in a later PR, once every open PR carries this
block). Machine-shaped `key: value` lines, one per line — every vocabulary is
read live from the authority that already owns it, never copied here:

    class:      the CMR ladder rung (governance/conformance/policy.yaml
                `ladder`) — the rung this PR must meet, never lower than the
                rung governance/conformance/surfaces.yaml declares for any
                surface root this PR's diff touches (ADR-0010/0031).
    posture:    governance/tagging/taxonomy.yaml `posture` values, comma-list
                if more than one applies. `no-human-needed` and `human-gated`
                are mutually exclusive.
    lifecycle:  governance/tagging/taxonomy.yaml `lifecycle` values.
    pillar:     the `pillar:*` label family (governance/tagging/taxonomy.yaml
                `pillar` values).
    pattern:    the pattern/rule this PR applies or extends — a `PP-nn` id
                (docs/PYTHON-PATTERNS.md), `SP-nn` (docs/SHELL-PATTERNS.md), a
                `GR-nn` golden rule (AGENTS.md), an `ADR-nnnn` decision record
                (docs/decision-records/), or `none`.
    lane:       `issue-<n>` (this PR's isolation lane branch) or `direct` for
                an owner merge with no lane branch — must agree with the head
                branch actually pushed.
-->

class: <template|class|pattern|enterprise|faang|elite>
posture: <overall|saas|iac|no-human-needed|human-gated>
lifecycle: <plan|build|verify|release>
pillar: <registry-profiling|model-gateway|state-machine|guardrails-security|observability-finops|identity-rbac|control-plane|governance|autonomous-ops>
pattern: <PP-nn|SP-nn|GR-nn|ADR-nnnn|none>
lane: <issue-<n>|direct>

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

## Surface class

- [ ] This PR does not change a governed surface's measured class (docs/SURFACE-CLASS.md).
- [ ] This PR changes one — I updated docs/SURFACE-CLASS.md in this same PR.

## Evidence

Two outputs, each by its real command: the acceptance criteria the issue named,
and a negative control proving the same check can FAIL. A check whose pass and
fail paths collapse into one exit code is a formality (AGENTS.md golden rule 8),
so the negative control is the half that makes the acceptance half mean anything.

### Acceptance output

```
$ <the exact acceptance command the issue named>
<its real output, pasted — a summary or a claimed count is not evidence>
```

### Negative-control output

```
$ <the same check against a deliberately broken input>
<its real output, pasted, showing the refusal and naming what it refused>
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
