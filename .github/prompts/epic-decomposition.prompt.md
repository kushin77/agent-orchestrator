---
description: "CMR epic-decomposition role prompt. Goal-first. Turn one intent into parallel, collision-free lanes (EPIC-*.md issue blocks with disjoint owns-globs). Fill {{...}} fields, then execute."
harvested_from: "docs/PROGRAM-MANAGEMENT.md (ability 2) + docs/EXECUTION-PLAN.md lane doctrine"
---

# ROLE: EPIC-DECOMPOSITION — {{epic_id}}

## Connections

- **Owner-lane:** qa-sme
- **Class:** class
- **Connects-to:** consumes=none; called-by=none; gates=none
- **Env:** none
- **Updated-by:** qa-sme (2026-09-12)
- **Landed-by:** 7c2a6b7

## Goal
Decompose `{{intent}}` into `board/epics/EPIC-*.md` issue blocks such that the
resulting issues run in **parallel lanes with disjoint owns-globs** — the execution
contract: one issue = one subagent = one lane, no two lanes share a file glob.

## Constraints (non-negotiable)
- Lanes and owns-globs are read from `fleet/MANIFEST.tsv`, never invented; verify
  with `bash fleet/dispatch.sh conflicts`.
- Every issue block carries `Labels`, `Milestone`, `Depends`, and a real `Verify:`
  command (a gate that cannot fail is a formality — GR-12).
- Decisions route through architecture-sme (MAX, decisions only); build issues go to
  the owning lane's SME at its manifest tier.
- Everything lands on the board (GR-20); run `board/materialize.sh issues --dry-run`
  before any write.

## Context
- CMR epics in `board/epics/` are the source of truth; `board/materialize.sh` syncs
  them to the GitHub board. Issue: {{issue}} (R# {{req}}).
- The manifest (`fleet/MANIFEST.tsv`) is generated from the board and re-derived at
  each train boundary.

## Steps
1. Read `docs/PROGRAM-MANAGEMENT.md` (ability 2), `docs/EXECUTION-PLAN.md`, and the intent.
2. Identify the lanes and SMEs; assign disjoint owns-globs.
3. Write the epic issue blocks (goal-first Story + Acceptance + Verify per issue).
4. Dry-run materialization and confirm no lane collision and a clean parse.

## Verify
- `bash fleet/dispatch.sh conflicts <milestone>` reports no collisions.
- `board/materialize.sh issues --dry-run` parses the new blocks cleanly.
- Paste each command and its output as evidence — never an unverified "done".

No-questions doctrine: apply `docs/DEFAULTS.md`; escalate only per GR-22 (secrets / apply / merge / irreversible).
