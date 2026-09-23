---
description: "Use when: debugging a failure, tracing a symbol or call path, answering a question about behaviour, or needing knowledge from another repo/module in the fleet. Renders CMR's GR-17 local-code-first rule (search this repo's own code first; use the CMR indexer KB for cross-repo or org-wide questions)."
applyTo: "**"
---

# Local-code-first — CMR (GR-17)

> Copilot-discoverable rendering of `guardrails/instructions/local-code-first.md`
> (canonical, CMR-1006). Rule statement: `GOLDEN-RULES.md` GR-17. The conformance
> gate's `local-first` signal fails any governed repo whose instruction layer lacks
> this directive, so the directive must stay present, not merely implied.

## The rule

**Search the repo's own code first.** When debugging, the local pass — grep for the
symbol, read the call path, run the failing test, read the existing docs — precedes every
other step. For cross-repo or org-wide knowledge, query the **CMR indexer KB (MCP)**
instead of guessing, re-cloning, or asking a human to remember.

## What to do

1. Debug the repo being edited locally first. Evidence from *this* repo before conclusions:
   `grep`/search for the symbol, read the implementation, run the failing test.
2. For anything outside the repo (another module, a shared contract, fleet history),
   query the CMR indexer KB (MCP) for the indexed answer.
3. Cite both: local evidence from the repo, org-wide evidence from the indexer.

## What not to do

- Do not skip the local pass and jump straight to the indexer (or to guessing) when the
  answer is in the repo being edited.
- Do not ignore the indexer for cross-repo questions and re-derive knowledge the index
  already holds.

## FinOps tie-in

Check the known-answer cache (L1) and this indexer KB **before** dispatching a subagent to
re-derive an answer — the cheap-tier-before-dispatch doctrine lives in
`docs/MODEL-PROFILES.md` → "Known-answers, cache & frontloading (DR-069)".

## Validation

Run the conformance gate (`make conformance`, `guardrails/check-conformance.sh`); its
`local-first` signal fails a governed repo whose instruction layer lacks this directive.
