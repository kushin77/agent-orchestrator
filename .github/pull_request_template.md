## Summary
<!-- What + why, one or two sentences. Link the issue/R# this closes. -->

## References (GR-2 / XR-004)
<!-- Typed cross-referencing: commits reference the issue; PRs close it. -->
- Closes #<n>  (cross-repo: `Closes <owner>/<repo>#<n>`)
- Refs <owner>/<repo>#<n>  (commit messages reference the issue this PR implements)

## Verification
- [ ] `make verify` passes (paste the command + key output below)
- [ ] `gitleaks detect -c .gitleaks.toml` clean — no secrets in the diff

## AI-assistance checklist (REQUIRED for every AI-authored or AI-assisted PR)
- [ ] AI-assisted: yes / no — declare explicitly (an AI-authored PR is held to the SAME bar as human work)
- [ ] Runtime declared: Claude Code / Copilot / Cursor / DeepSeek fleet + model tier (L0/L1/L2)
- [ ] `make verify` passed before opening; output attached under Verification
- [ ] No self-merge (GR-4): a reviewer merges, never the AI author
- [ ] Shared-module change: blast-radius note below (GR-11 — which dependents/consumers are affected)
- [ ] Canibalized asset provenance recorded in `canibalization/INDEX.md` / `registry.csv` (GR-10)

## Change type
- [ ] Infra / governed config (`infra/**`) — human sign-off obtained (Terraform plan reviewed)
- [ ] Shared module / template — blast-radius note below (which consumers are affected)

## Blast radius
<!-- For shared-module/template changes: list affected spokes/consumers. -->

## Checklist
- [ ] Smallest focused diff; no unrelated edits
- [ ] No `TODO`/`FIXME`/debug leftovers, unused code, or commented-out blocks
- [ ] Canibalized assets carry provenance in `canibalization/INDEX.md` / `registry.csv`
