# Release Pack Provenance (registry/packs)

Every release snapshot under `registry/packs/releases/` is **derived from**
(not blindly copied from) the repo's own merged artifacts plus the harvested
fleet/hub assets below, per the repo provenance doctrine (GR-10;
`docs/CANNIBALIZATION.md`, issue #8). Pack schema/category/lifecycle field
vocabulary is normalized to `registry/packs/pack-catalog.yaml` and the closed
sibling-lane catalogs (profiles, personas, prompts, guardrails/policy,
identity/rbac). A pack **references and bundles** those artifacts; it never
redefines their vocabularies.

| Release (`pack` `version`) | Bundled artifact (repo path) | Derived from (source of the vocabulary) |
|---|---|---|
| `worker-platform` 1.0.0 | profile `coder@1.0.0` (`registry/profiles/seeds/coder.1.0.0.yaml`, immutable) | `kushin77/CMR` `catalog/schemas/module.schema.json` (module identity/versions), `kushin77/shared-frontend` `modules/schema.json` (id/category/status/upstream), `kushin77/leaderboard` `docker/worker-fleet/personas.yaml` (executor worker) |
| `worker-platform` 1.0.0 | persona `coder` (`registry/personas/cards/coder.yaml`) | persona-card model from `kushin77/CMR` + `kushin77/leaderboard` persona vocab (issue #11) |
| `worker-platform` 1.0.0 | prompt `classify-route@v1` (`registry/prompts/modules/classify-route.v1.yaml`) | prompt-module convention from `gmail-agent` `prompts/<task>/v1.ts` (issue #13) |
| `data-ops` 1.0.0 | profile `data-agent@1.0.0` + persona `data-agent` + prompt `summarize@v1` | `kushin77/capital-underwriting` `infra/docker/worker-fleet/personas.yaml` (ds-worker pool) + `registry/profiles` + `registry/personas` + `registry/prompts` |
| `orchestrator-ops` 1.0.0 | profile `orchestrator@1.0.0` + persona `orchestrator` + prompt `classify-route@v1` | `kushin77/leaderboard` orchestrator persona + `registry/profiles` + `registry/personas` + `registry/prompts` |
| all | policy + tool manifests (pack-authored content derived from each bundled profile's `guardrailPolicyRef` / `toolAllowlist`) | `kushin77/CMR` `guardrails/policy/controls.yaml` (policy registry) + `registry/profiles/catalog.yaml` tool catalog (issue #9) |

## Signed-attestation vocabulary (consumed, not redefined)

| Source (`.research/` mirror) | Pattern adapted | Where it landed |
|---|---|---|
| `CMR` `catalog/schemas/module.schema.json` | namespaced module id, `schema` version const, `versions.latest`, `dependencies`/`consumers` crossref model | `agent-pack.schema.json` (`schema: agent-pack/v1`, `dependencies`, catalog crossref in `registry.py`) |
| `CMR` `catalog/schemas/crossref.schema.json` + `catalog/crossref/crossref.json` | dependency/dependent catalog cross-reference | `PackRegistry.crossref` |
| `CMR` `catalog/consumption/README.md` | consumption contract — who consumes which version, by pin | `PackRegistry` consumption ledger + `record_install` |
| `CMR` `registry/attestation.sh` | signed, auditable per-version bundle (PUBLISHER kid, algorithm, timestamp, signature) | `attestation.py` (PS256 = RSASSA-PSS/SHA-256), `publisher-key.pem`, `releases/*` attestation blocks |
| `CMR` `catalog/events/events.schema.json` | closed registry event kinds (publish/consume/upgrade-pr/drift) | `pack-event.schema.json` + `pack_events.py` closed enum (plan/publish/pause/resume/retire/install/upgrade/rollback/drift/consume) |
| `CMR` `registry/upgrade/upgrade.sh` + `catalog/validate.py` | upgrade planning + honest module-catalog validator (real exit codes) | `installer.py` upgrade/rollback + `validate.py` (exit 0/1/2) |
| `kushin77/shared-frontend` `modules/schema.json` | module/registry entry contract: `id`/`category`/`status`/`upstream` | `agent-pack.schema.json` fields + lifecycle enum (planned/live/paused/retired) |
| `kushin77/leaderboard` `modules.manifest.json` + `templates/consumer-repo` | upstream/consumer manifest + drift-detection posture | `installer.py` drift detection (`verify_installed`) + `versions/manifest.yaml` immutable ledger |
| `kushin77/shared-governance` `GLOBAL_STANDARDS/schemas/*` | task/agent-action schema discipline (closed, versioned) | closed vocabularies + schema-version const in `agent-pack.schema.json` |

## Signing key

- Public key committed: `registry/packs/publisher-key.pem` (kid
  `ao-pack-publisher-v1`). A public key is not a secret (GR-6) and ships so
  releases can be verified offline by `validate.py` and at install time by
  `installer.py`.
- The private key is **never committed**; it is injected at publish time
  (GR-6: env/secret manager only). Release signatures are produced during the
  publish pipeline, not from repository state.
