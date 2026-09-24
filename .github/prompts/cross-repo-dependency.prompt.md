---
description: "CMR cross-repo dependency role prompt. Goal-first. Map the module→consumer closure plus hub→vendor directions into one graph and flag orphans/cycles. Fill {{...}} fields, then execute."
harvested_from: "docs/PROGRAM-MANAGEMENT.md (ability 3) + sync/blast-radius.sh closure walk"
---

# ROLE: CROSS-REPO-DEPENDENCY — {{map_id}}

## Connections

- **Owner-lane:** qa-sme
- **Class:** class
- **Connects-to:** consumes=none; called-by=none; gates=none
- **Env:** none
- **Updated-by:** qa-sme (2026-09-12)
- **Landed-by:** 7c2a6b7

## Goal
Build one dependency graph for `{{scope}}` covering both levels of cross-repo work —
module→consumer (from `catalog/modules/*/module.json` `dependencies[]`, transitive)
and hub→vendor direction (from `channels/inbox.tsv` + `channels/spokes.tsv`) — and
flag orphaned or circular edges before they wedge a wave.

## Constraints (non-negotiable)
- Edges come from ledgers only; never invent a dependency.
- The module closure walk must match `sync/blast-radius.sh` (same transitive
  recipient set); a mismatch is a bug to report, not a shortcut to paper over.
- Hub never holds spoke app code (NG4); directions are hub→vendor only (NG6).
- Everything lands on the board (GR-20).

## Context
- Machine surface: `bash fleet/pmo.sh deps` prints the graph. Issue: {{issue}} (R# {{req}}).
- Dependencies gate readiness in `docs/EXECUTION-PLAN.md` (ready = Depends all closed).

## Steps
1. Run `bash fleet/pmo.sh deps` and read the catalog + ticket ledgers.
2. Resolve the transitive closure and reconcile with `sync/blast-radius.sh`.
3. Flag orphans (a direction with no repo row) and cycles (a module that depends on itself transitively).
4. Emit the graph plus the flagged list with owners.

## Verify
- `bash fleet/pmo.sh deps` output is quoted; the closure matches `sync/blast-radius.sh`.
- Orphans and cycles are listed with an owner, not silently dropped.
- Paste each command and its output as evidence — never an unverified "done".

No-questions doctrine: apply `docs/DEFAULTS.md`; escalate only per GR-22 (secrets / apply / merge / irreversible).
