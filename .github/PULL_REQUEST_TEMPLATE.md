<!--
agent-orchestrator pull request — the merge contract (issue #288).
The body obligations are load-bearing: `scripts/check-pr-contract.sh` reads this
file, so removing a section is a gate failure, not a style choice.
-->

## What and why

<!-- One paragraph: the change and the issue it closes. -->

Closes #

## AI-assistance

AI-assistance: <runtime> (<mode>)

<!-- Required for AI-originated work (docs/GOVERNANCE.md section 2). -->
<!-- Example: AI-assistance: Copilot (Relentless, flash/LOW) -->

## Verify (real output, not a claim)

A green claim is not evidence (GR-12). Paste the actual command and its actual
tail, including the `verify: PASS (N of N checks)` line.

```
$ make verify
<paste the real output>
```

Every commit carries its ticket reference as a trailer:

```
Refs kushin77/agent-orchestrator#<n>
```

## Pre-existing red (only when the gate is red)

A failure is pre-existing only when it is **reproduced** on a clean checkout of
the base commit. Asserting it is not evidence; a quoted command and its output
is. Fill this in or delete the section:

```
Reproduce:
$ git worktree add --detach /tmp/proof origin/master && cd /tmp/proof && make verify
<paste the output that shows the same failure on the base commit>
```

## Checklist

- [ ] Every commit carries the `Refs kushin77/agent-orchestrator#<n>` trailer
- [ ] `Closes #<n>` names the issue this PR closes
- [ ] `AI-assistance:` is declared above
- [ ] `make verify` is green (output pasted above)
- [ ] No secrets, no `vendor/` edits, no `.research/` commits
