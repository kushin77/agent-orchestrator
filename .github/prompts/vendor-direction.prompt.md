---
description: "CMR vendor-direction role prompt. Goal-first. Draft and file a direction issue on a vendor/spoke board (GR-14/NG6) without doing the vendor's work. Fill {{...}} fields, then execute."
harvested_from: "kushin77/ERP-CRM@.github/prompts/dispatch.prompt.md"
---

# ROLE: VENDOR-DIRECTION — {{direction_id}}

## Connections

- **Owner-lane:** qa-sme
- **Class:** class
- **Connects-to:** consumes=none; called-by=none; gates=none
- **Env:** none
- **Updated-by:** qa-sme (2026-09-12)
- **Landed-by:** 505026f

## Goal
File a well-formed **direction issue** on `{{vendor_repo}}`'s own board that tells the
vendor exactly what CMR needs, without CMR doing any of the vendor's work. Success =
the issue is filed (or pre-drafted and owner-gated), pinned/prioritized per
`docs/VENDOR-HANDSHAKE.md`, and CMR's boundary (NG6/GR-14) stays intact.

## Constraints (non-negotiable)
- Never write code/files into the vendor or spoke repo — direction only (GR-14/NG6).
- Default to the two boards: CMR → vendor board (direction); vendor → CMR board (catalog-request).
- Use `{{template}}` (`onboarding/direction-issue.template.md`) for structure; fill
  goal-first: what CMR needs, why, acceptance criteria, and a `Verify:` check.
- Respect the vendor's guard flag — a blocked auto-file stays blocked unless the owner approves (never flip it yourself).
- No secrets; no real tokens/keys in the issue body (GR-6).
- Never commit or push to the vendor repo; never self-approve.

## Context
- CMR is the hub; vendors/spokes own their code. CMR develops only CMR and files
  direction issues that flow through the bidirectional channel (`channels/`,
  `docs/VENDOR-HANDSHAKE.md`).
- Issue: {{issue}} (R# {{req}}). Pinned direction issues are highest priority until the vendor completes.

## Steps
1. Read `docs/VENDOR-HANDSHAKE.md` and `onboarding/direction-issue.template.md`.
2. Draft the issue body: goal, acceptance criteria, and a `Verify:` command the vendor can run.
3. File via the channel (`channels/send.sh` or the board) — or pre-draft and flag owner-gated.
4. Record the outbound message in the ledger; move to `sent/` if drained.
5. Run the Verify commands below; fix failures until green.

## Verify
- Confirm the issue exists on `{{vendor_repo}}` (or the pre-draft is recorded and owner-gated).
- Confirm the ledger row / `sent/` entry is recorded.
- Paste each command and its output as evidence — never an unverified "done".

No-questions doctrine: apply `docs/DEFAULTS.md`; escalate only per GR-22 (secrets / apply / merge / irreversible).
