---
description: "Standing debugging rule (GR-17): search the repo's own code first; for cross-repo / org-wide knowledge query the CMR indexer KB (MCP)."
applyTo: "**"
---

# Local-Code-First Debugging — CMR

## Connections

- **Owner-lane:** qa-sme
- **Class:** class
- **Connects-to:** consumes=none; called-by=none; gates=none
- **Env:** none
- **Updated-by:** qa-sme (2026-09-12)
- **Landed-by:** 9b5f52e
- **enforcer:** make conformance — conformance carries the local-first signal


> Standing rule for every repo CMR governs (GR-17, CMR-1006). Canonical
> statement: `GOLDEN-RULES.md` → GR-17; the conformance gate's `local-first`
> signal verifies the directive is present. The FinOps side of this doctrine —
> checking the known-answer cache (L1) + this indexer KB at LOW tier *before*
> dispatching a subagent to re-derive an answer, frontloaded dispatch briefs,
> and warm-start packs for new sessions — is `docs/MODEL-PROFILES.md` →
> "Known-answers, cache & frontloading (DR-069)"; the conformance gate's
> `finops-doctrine` signal verifies that section is present.

## The rule

Search the repo's own code first when debugging — the **local-code-first** pass
(grep, usage search, tests, existing docs) precedes any other step. For
cross-repo or org-wide knowledge, query the **CMR indexer KB (MCP)** instead of
guessing, re-cloning, or asking someone to remember.

## What to do

1. Debug the repo being edited locally first: grep for the symbol, read the
   code path, run the failing test — evidence from this repo before conclusions.
2. For anything outside the repo (another module, a shared contract, fleet
   history), query the CMR indexer KB (MCP) for the indexed answer.
3. Cite both: local evidence from the repo, org-wide evidence from the indexer.

## What NOT to do

- Do not skip the local pass and jump straight to the indexer (or to guessing)
  when the answer is in the repo being edited.
- Do not ignore the indexer for cross-repo questions and re-derive knowledge
  the index already holds.

## Validation

- `make verify` runs the conformance gate; the `local-first` signal fails any
  governed repo whose instruction layer lacks this directive (GR-17).
