# CMR gap analysis — googlecloudplatform onboarding (grade → govern → catalog → consume → verify → flip)

Gap analysis for `kushin77/googlecloudplatform` (vendor module, GCP landing-zone
IaC) against the CMR adoption spine in `vendor/CMR/docs/ADOPTION.md`. Origin:
extraction from `kushin77/ERP-CRM` `landing-zone-migration/` (ERP-DISSECT-001
P1), tracked as `CMR:ONBOARD-0006`. This is a read-only survey — no vendor
code is edited (vendor/CMR is a pinned submodule; `googlecloudplatform` itself
is a separate repo not checked out here).

## Stage-by-stage status

### Grade — CLOSED (#1733)
Landed upstream in `kushin77/CMR` pull request 1022 (merged 2026-09-21T23:48Z),
"docs(health): record googlecloudplatform baseline conformance grade":
appended a `conformance` row to `docs/health-report.tsv` and a matching
`conformance.kushin77/googlecloudplatform` object to `docs/health-report.json`,
sourced from `guardrails/check-conformance.sh`. Recorded result: **FAIL** —
3 pass, 11 failed (4 vendor-owned: `module.json`, `release`, `gdc-manifest`,
`architecture-manifest`; 7 hub-owned via standards-bundle), 2 advisory, 2 skip.
That satisfies #1733's Done line verbatim (one row, security-row format, both
files). Because this repo does not check out `kushin77/googlecloudplatform`
or carry a fresh `vendor/CMR` pin past that merge, the row above is quoted
from the upstream PR diff rather than visible in this checkout — the submodule
pointer here predates PR 1022 and its pin bump is tracked separately.

A follow-up, `kushin77/CMR` pull request 1025 (merged 2026-09-21T23:54Z),
"docs(governance): re-grade kushin77/googlecloudplatform after
dependabot/gitleaks bundle", re-ran the same grade after the Govern-stage
bundle below landed: vendor-owned failures unchanged, two hub-owned signals
(`dependabot`, `gitleaks-config`) flipped from FAIL to PASS, 9 of 17 signals
remain FAIL overall.

### Govern — IN PROGRESS (#1734 stays open)
`kushin77/googlecloudplatform` pull request 3 (merged 2026-09-21T23:48Z),
"chore(governance): add dependabot + gitleaks config", added
`.github/dependabot.yml` (terraform ecosystem, weekly) and a root
`.gitleaks.toml` (extends gitleaks default ruleset, narrow allowlist for
placeholder markers and two portal localStorage keys, `stopwords` for the
"budgets/FinOps" false positive, path-excludes build dirs and nested
worktrees). That closes the two vendor-side gaps this doc flagged
(no dependabot config, no gitleaks config).

**Still missing:** `kushin77/CMR` pull request 1025's own diff records that
`controller/governance.tsv` row 5 remains `applied=false` — "governance
bundle merged 2026-09-21 (dependabot+gitleaks now PASS, 9 of 17 signals
remain FAIL); Terraform apply itself (CMR-104/111) not yet run." The Terraform
governance module has not been applied, so #1734's Done line (flip row 5 to
`applied=true`) is not satisfied. GR-5 bans ad-hoc `terraform apply` from an
agent session, so this cannot be closed by hand either — #1734 stays open,
tracking only the remaining Terraform-apply step.

### Catalog
- **Exists:** `channels/sent/googlecloudplatform-0001-module.md` and the spokes
  row describing the intended `module_id: googlecloudplatform`, `type: infra`,
  `distribution.terraform: infra/terraform`, features (landing-zone,
  audit-logging, gcp-rbac, migration-ops).
- **Missing:** no `catalog/modules/googlecloudplatform/module.json` exists —
  `vendor/CMR/catalog/modules/` lists only `code-indexing`, `diagrams`,
  `erp-crm`, `googleworkspace`, `saas-rbac`, `shared-frontend`.
- **Smallest fix:** vendor files a catalog-request (per the onboarding doc's
  "Next step") after it conforms and tags `v0.1.0`; CMR then registers the
  `module.json`. Sequenced after Grade/Govern, not independent of them.

### Consume
- **Exists:** the intended consumer is named — ERP-CRM's landing-zone IaC,
  per ERP-DISSECT-001 P1 (`ERP-CRM#326`) — but nothing in this repo pins the
  module yet.
- **Missing:** no consumer `module.json` `dependencies[]` entry anywhere in
  this checkout references `googlecloudplatform`.
- **Smallest fix:** blocked on Catalog; once `module.json` exists, ERP-CRM
  adds the pin (out of scope for this repo/lane — ERP-CRM is a separate repo).

### Verify
- **Exists:** nothing — no conformance run result recorded for this module.
- **Missing:** the target-11/11 conformance record cited by the wave spine
  precedent (saas-rbac/shared-frontend) does not exist for this module.
- **Smallest fix:** follows Grade re-run after Govern lands; same script,
  recorded result.

### Flip
- **Exists:** `channels/spokes.tsv` row 26 — `onboarded=false`.
- **Missing:** the flag flip itself, and `standards-sync/onboard.sh` code
  delivery is correspondingly not enabled for this module.
- **Smallest fix:** a one-line TSV edit once Verify passes — mechanical, no
  new issue needed.

## Issue coverage

Searched `gh issue list --state all --search "googlecloudplatform"`,
`"CMR:ONBOARD-0006"`, and `"gcp landing-zone"`. Only #1665 itself (this gap
issue) currently exists; no other open or closed issue tracks any of the six
stages for this module. Two issues are filed below for the uncovered, actionable (non-vendor-gated)
gaps; Catalog/Consume/Flip are sequentially blocked on those and on
vendor-side conformance (NG6 boundary — CMR does not do that work), so they
are not filed as separate issues yet.

## Roadmap (dependency-ordered, file-disjoint lanes)

1. **Lane A — Grade (see Issue map):** run `guardrails/check-conformance.sh`
   against `kushin77/googlecloudplatform`; record the score in
   `vendor/CMR/docs/health-report.tsv` + `.json`. Touches only those two files.
2. **Lane B — Govern (see Issue map):** apply the Terraform governance module
   (CMR-104/111) once vendor adds `.github/dependabot.yml` + root
   `.gitleaks.toml` and Lane A confirms a baseline; flip
   `controller/governance.tsv` row 5 `applied=true`. Depends on Lane A (grade
   informs whether governance can apply cleanly).
3. **Lane C — Catalog + Verify + Flip:** after vendor tags `v0.1.0` and files
   the catalog-request, register `catalog/modules/googlecloudplatform/module.json`,
   re-run conformance (target 11/11), and flip `channels/spokes.tsv` row 26
   `onboarded=true`. Depends on Lane B. Consume (ERP-CRM pin) happens in the
   ERP-CRM repo afterward, out of this repo's lane set. No issue filed yet —
   blocked on vendor-side work outside this repo's authority.

## Issue map

| Issue | Stage | Scope |
| --- | --- | --- |
| #1665 | (this gap issue) | Tracking issue for the analysis above |
| #1733 | Grade | Record baseline conformance grade |
| #1734 | Govern | Apply governance bundle (dependabot + gitleaks + Terraform governance) |
