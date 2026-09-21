# CMR gap analysis — googlecloudplatform onboarding (grade → govern → catalog → consume → verify → flip)

Gap analysis for `kushin77/googlecloudplatform` (vendor module, GCP landing-zone
IaC) against the CMR adoption spine in `vendor/CMR/docs/ADOPTION.md`. Origin:
extraction from `kushin77/ERP-CRM` `landing-zone-migration/` (ERP-DISSECT-001
P1), tracked as `CMR:ONBOARD-0006`. This is a read-only survey — no vendor
code is edited (vendor/CMR is a pinned submodule; `googlecloudplatform` itself
is a separate repo not checked out here).

## Stage-by-stage status

### Grade
- **Exists:** a profile record — `vendor/CMR/onboarding/googlecloudplatform/0001-vendor-onboarding.md`
  ("research-verified 2026-09-07") and a security snapshot in
  `vendor/CMR/docs/health-report.tsv` (code-scanning disabled, secret-scanning
  disabled, dependabot 0, no advisories). Neither is a conformance-suite run —
  `vendor/CMR/guardrails/check-conformance.sh` has not been pointed at this
  repo; no grade score is recorded anywhere in `vendor/CMR/catalog/` or
  `vendor/CMR/docs/health-report.*`.
- **Missing:** a recorded baseline conformance grade for the target repo.
- **Smallest fix:** run `check-conformance.sh` against `kushin77/googlecloudplatform`
  and append the score to `health-report.tsv`/`.json` (one row, matching the
  existing security-row format).

### Govern
- **Exists:** `vendor/CMR/controller/governance.tsv` row 5:
  `kushin77/googlecloudplatform / vendor / applied=false / declared — GCP infra
  module ... applied by Terraform governance (CMR-104/111)`.
- **Missing:** the guardrails/policy bundle has not been applied (`applied=false`).
  The onboarding doc also flags two concrete gaps the vendor repo itself must
  close first: no `.github/dependabot.yml`, no root `.gitleaks.toml`.
- **Smallest fix:** flip `applied` once the Terraform governance module (already
  named in the row) runs against the vendor repo; that is vendor-side work
  gated by NG6 (CMR never writes vendor code), so this repo's part is limited
  to recording the applied state once the vendor confirms.

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
