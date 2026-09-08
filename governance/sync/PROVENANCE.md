# Provenance — governance/sync (issue #44)

Every asset adapted into this subtree records its source (GR-10 / AO-GR-10;
index: [`docs/CANNIBALIZATION.md`](../../docs/CANNIBALIZATION.md)). Patterns
were **adapted**, not copied: the product surface here is a Python 3 stdlib
engine for a SaaS control plane, the sources are shell/JSON governance tooling
in other fleet repos. Nothing here is byte-identical to a source.

## Adapted sources

| # | Source (repo / path) | License posture | What was adapted | Where it landed |
|---|---|---|---|---|
| 1 | `kushin77/CMR` `sync/vendor-manifest.json` + `vendor-manifest.schema.json` (EPIC-04/CMR-401, harvested from shared-frontend vendor-manifest) | fleet-internal | Consumers -> pins vocabulary; schema-identity discipline; `status` up-to-date/upgrade/drift | `provenance.py` manifest schema (`ao.sync/provenance-manifest-v1`), asset entry fields |
| 2 | `kushin77/CMR` `sync/drift-check.sh` (CMR-404) | fleet-internal | Report vocabulary (summary + findings); report-only/dry-run posture; honest remediation lines | `drift.py` per-asset findings + summary; `cli.py` drift report |
| 3 | `kushin77/CMR` `sync/blast-radius.sh` (EPIC-08/CMR-801) | fleet-internal | Transitive dependent closure from a catalog; run every dependent's gate before a change merges; `--dry-run` computes without executing | `blast_radius.py` closure + consumers-affected report; `SyncEngine` dry-run default |
| 4 | `kushin77/CMR` `sync/drift-apply.sh` | fleet-internal | Apply with approval for breaking changes | `sync_plan.py` explicit-apply seam (dry-run default, `--apply`) |
| 5 | `kushin77/CMR` `registry/upgrade/upgrade.sh` + `lock.sh` | fleet-internal | Upgrade path with rollback | `AssetMaterializer` backup/restore rollback + `SyncEngine` rollback-on-failure |
| 6 | `kushin77/shared-frontend` `docs/PROVENANCE.md` + design-tokens README provenance table | fleet-internal | Single authoritative pin per shared asset; derived records MUST agree and are checked, never hand-edited; MATCH/DRIFT/MISSING verdicts where missing is never clean | `provenance.validate_manifest` missing-pin flag (negative-tested); `drift.py` CLEAN/DRIFT/CANNOT_ASSESS with `content_sha256` agreement check |
| 7 | `kushin77/CMR` `docs/decision-records/ADR-0005-versioning-policy.md` | fleet-internal | SemVer tag-as-truth; consumers pin versions; no force-push breaking change | Manifest `version` field; version-drift detection vs a desired target |
| 8 | `kushin77/leaderboard` `scripts/vendor/*`, `scripts/sync/*`, `scripts/migration/*` | fleet-internal | Sync daemon / adopt / reconcile cadence concept | `SyncEngine` scheduled-reconciler contract; README phase-8 framing |
| 9 | `kushin77/CMR` `sync/vendor-feedback.sh`, `sync/pr-back-intake.sh`, `sync/gdc-feed.sh` | fleet-internal | Consumer feedback (lessons) flows back to pack authors | README "Downstream consumers" + `note` field on manifest entries (bidirectional seam noted for pack authors) |
| 10 | This repo `guardrails/honesty` (issue #28) tri-state aggregate | repo-internal | CANNOT-ASSESS is never a pass; aggregate verdict discipline | `model.DriftState` + `model.aggregate_states` |

## Consumed seams (read-only imports, never edited here)

| Seam | Repo path | How consumed |
|---|---|---|
| `Installer.sync_plan` + `SyncPlanAction` | `registry/packs/installer.py` (issue #40) | `sync_plan.PackSyncAdapter.actions_for` + `ReconcileAction`; integration-tested against the real class |
| `Installer.rollback` | `registry/packs/installer.py` (issue #40) | documented rollback op target (`test_pack_integration.py`) |
| Consumer sync wiring (`ao.sync-config/v1`, sha256 MANIFEST) | `control-plane/sdk/template/consumer-repo/` (issue #41) | mirrored `content_sha256` drift discipline |

## Seed ecosystem provenance

`seeds/` is a synthetic offline demo; the canonical assets model realistic
shared sources and each seed consumer manifest records its own
`source_repo`/`source_path`/`version`/`sha` per asset:

| Seed asset | Canonical source (modelled) | Version |
|---|---|---|
| `core-guardrails` | `kushin77/CMR` `shared/core-guardrails.txt` | 1.0.0 |
| `pack-sync-engine` | `kushin77/CMR` `shared/pack-sync-engine.txt` | 2.1.0 |
| `portal-shell` | `kushin77/control-plane` `shared/portal-shell.txt` | 3.0.0 |
| `design-tokens` | `kushin77/shared-frontend` `shared/design-tokens.txt` | 1.4.0 |
| `unrelated-asset` (rogue fixture) | `kushin77/other-repo` `vendor/unrelated-asset.txt` | 0.1.0 |

The seed content pins are produced by the real `provenance.generate_manifest`
generator, so the demo is internally consistent by construction.
