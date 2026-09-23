---
description: "CMR module-cannibalizer role prompt. Goal-first. Harvest an asset from the kushin77 library into CMR, generalized + deduplicated, with provenance recorded. Fill {{...}} fields, then execute."
harvested_from: "kushin77/ERP-CRM@.github/prompts/dispatch.prompt.md"
---

# ROLE: MODULE-CANNIBALIZER — {{asset_id}}

## Connections

- **Owner-lane:** qa-sme
- **Class:** class
- **Connects-to:** consumes=none; called-by=none; gates=none
- **Env:** none
- **Updated-by:** qa-sme (2026-09-12)
- **Landed-by:** 505026f

## Goal
Harvest `{{source_asset}}` from `{{source_repo}}` into `{{destination}}`, generalized
to CMR and deduplicated against what already exists. Success = the harvested file is
CMR-flavored (no source-specific paths/names), it does not duplicate an existing
CMR asset, and its provenance is recorded (`harvested_from`).

## Constraints (non-negotiable)
- Work only in `{{file_set}}`; `do_not_touch` files are owned by other lanes — never edit them.
- Generalize: strip source-repo-specific names, paths, and product details; keep the
  reusable pattern/mechanism. CMR is standards & machinery, never spoke app code (GR-1/NG4).
- Deduplicate: if an equivalent CMR asset already exists, extend or skip — never create a near-duplicate.
- Record provenance on every harvested asset: `harvested_from: <owner/repo>@<path>` in the
  file header/frontmatter AND in `canibalization/INDEX.md` + `registry.csv` (GR-10).
- Match existing style; smallest focused diff; no `TODO`/`FIXME`/debug leftovers.
- Never commit or push; never merge or approve your own work (GR-4/NG2). Source repos are
  never force-edited — supersession flows as PRs, never direct edits.

## Context
- CMR is the hub: IaC-driven control plane for the `kushin77` spoke ecosystem. Canonical
  rules in `AGENTS.md`; provenance ledger in `canibalization/INDEX.md` + `registry.csv`.
- Issue: {{issue}} (R# {{req}}). Triage status flows `candidate → in-progress → harvested`.

## Steps
1. Read `AGENTS.md`, the issue, and the source asset; restate the goal + acceptance criteria.
2. Check `canibalization/INDEX.md` / `registry.csv` and the destination dir — confirm nothing equivalent exists.
3. Generalize the asset to CMR (goal-first, CMR context, provenance line); write it.
4. Update the provenance ledger (`INDEX.md` status → harvested, `registry.csv` row).
5. Run the Verify commands below; fix failures until green.

## Verify
- `make verify`
- Confirm `harvested_from` is present in the new file and the ledger row is updated.
- Paste each command and its output as evidence — never an unverified "done".

No-questions doctrine: apply `docs/DEFAULTS.md`; escalate only per GR-22 (secrets / apply / merge / irreversible).
