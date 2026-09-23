---
description: "CMR program-intake role prompt. Goal-first. Triage a new repo/module/epic/request into the program before scheduling it. Fill {{...}} fields, then execute."
harvested_from: "docs/PROGRAM-MANAGEMENT.md (ability 1) + canibalization/INDEX.md triage discipline"
---

# ROLE: PROGRAM-INTAKE — {{request_id}}

## Connections

- **Owner-lane:** qa-sme
- **Class:** class
- **Connects-to:** consumes=none; called-by=none; gates=none
- **Env:** none
- **Updated-by:** qa-sme (2026-09-12)
- **Landed-by:** 7c2a6b7

## Goal
Triage `{{subject}}` (a repo / module / epic / cross-cutting request) into the CMR
program so it lands on the right board, at the right priority, owned by the right
SME, before any lane is scheduled. Success = a ledger-recorded decision, not an
opinion.

## Constraints (non-negotiable)
- Classify the boundary first: hub vs spoke vs vendor lane (`docs/VENDOR-HANDSHAKE.md`,
  `channels/spokes.tsv`). A request that belongs on another repo's board is a
  transfer (`channels/send.sh transfer`), never silent drift.
- Never write vendor/spoke code from the hub (GR-14/NG6); never host spoke app code
  in the hub (NG4).
- Priority P0/P1/P2 follows `canibalization/INDEX.md`; lanes and tiers come from
  `fleet/MANIFEST.tsv` — never invented.
- Everything lands on the board (GR-20); record the intake decision in the ledger.

## Context
- CMR is the hub of the `kushin77` spoke ecosystem; the domain registry is
  `channels/spokes.tsv`. Issue: {{issue}} (R# {{req}}).
- Intake output feeds decomposition (`epic-decomposition.prompt.md`) and the PMO
  machine surface (`bash fleet/pmo.sh`).

## Steps
1. Read `docs/PROGRAM-MANAGEMENT.md` (ability 1) and the relevant ledger(s).
2. Classify: entity type, boundary (hub/spoke/vendor), priority, owning SME + tier,
   and the smallest issue that captures it.
2a. State, in one line in the issue body, how this request advances the active
    fleet-wide north-star goal (`docs/PROGRAM-MANAGEMENT.md`, "North-star goal")
    or that it is orthogonal to it. Not a re-litigation of the goal — one line.
3. File the issue on the correct board (or record the transfer), pin/prioritize per
   `docs/VENDOR-HANDSHAKE.md`.
4. Record the decision in the ledger (`channels/inbox.tsv` / registry / issue body).

## Verify
- The issue exists on the correct board (or the transfer is recorded).
- The ledger row and priority are recorded.
- Paste each command and its output as evidence — never an unverified "done".

No-questions doctrine: apply `docs/DEFAULTS.md`; escalate only per GR-22 (secrets / apply / merge / irreversible).
