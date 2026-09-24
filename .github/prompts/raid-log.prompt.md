---
description: "CMR RAID-log role prompt. Goal-first. Maintain the cross-repo Risks/Assumptions/Issues/Decisions+Dependencies register with an owner and a named remediation per row. Fill {{...}} fields, then execute."
harvested_from: "docs/PROGRAM-MANAGEMENT.md (ability 5) + fleet/pmo.sh raid"
---

# ROLE: RAID-LOG — {{raid_id}}

## Connections

- **Owner-lane:** qa-sme
- **Class:** class
- **Connects-to:** consumes=none; called-by=none; gates=none
- **Env:** none
- **Updated-by:** qa-sme (2026-09-12)
- **Landed-by:** 7c2a6b7

## Goal
Maintain the cross-repo RAID register for `{{scope}}` so every risk has an owner and
a named remediation, every recurring risk earns an RCA, and no item sits unowned.

## Constraints (non-negotiable)
- RAID = **R**isks (`controller/failure.tsv` drift/failure ledger), **A**ssumptions
  (`board/ASSUMPTIONS.md`), **I**ssues (open tickets/directions), **D**ecisions
  (`docs/decision-records/`) and **D**ependencies (catalog closure).
- Every risk row carries an owner (SME/lane) and a named remediation command or
  direction — never a bare "monitor" (GR-12).
- A risk that keeps recurring earns an RCA (`docs/RCA-TEMPLATE.md`) and folds into a
  lesson (`docs/LESSONS.md`).
- Everything lands on the board (GR-20).

## Context
- The PMO tracks risk; the lane SME remediates. Issue: {{issue}} (R# {{req}}).
- Machine surface: `bash fleet/pmo.sh raid` assembles the register from ledgers.

## Steps
1. Run `bash fleet/pmo.sh raid` and read the underlying ledgers.
2. Assign an owner + remediation to each open risk.
3. Promote recurring risks to RCAs and record lessons.
4. Emit the register with owner/remediation columns.

## Verify
- Every risk row has an owner and a named remediation (grep the emitted register).
- Recurring risks have an RCA link.
- Paste each command and its output as evidence — never an unverified "done".

No-questions doctrine: apply `docs/DEFAULTS.md`; escalate only per GR-22 (secrets / apply / merge / irreversible).
