# Module brief — what every repo must carry, at which pin, and whether it is current

Composed by `integrations/paperclip/reporting/` (issue #447 — the paperclip reporting half, ADR-0012) from the module registry (issue #445) and the hub files that registry cites. Nothing in this document is re-derived: every line below cites the registry row or the cited path it resolves to.
Composition is deterministic — two runs over one revision are byte-identical — and `scripts/check-module-brief.sh` regenerates this document and refuses a stale one by name.

## 1. Sources — every claim below resolves to a registry row or one of these

| Source | Path | What it is | Cited as |
| --- | --- | --- | --- |
| module registry | `governance/modules` | the ecosystem module registry (issue #445): state, pin/rev, consumer assets, health, board ref | `governance/modules` |
| board snapshot | `.board/snapshot.json` | the committed board snapshot of this repository — cited as the board evidence surface, never asserted as a live state | `.board/snapshot.json` |
| hub catalog | `vendor/CMR/catalog` | the hub's mandatory registry and per-module manifests (the authority the registry reads) | `vendor/CMR/catalog` |
| hub revision | `vendor/CMR` | the pinned, read-only hub checkout the registry was built from | `vendor/CMR` |
The hub revision is b6c49aa03992dba9fe4b87b46104b8fc2f69f224 (hub-checkout); the board snapshot carries this repository's own board only, so a hub issue named below is **cited, never asserted** open or closed.

## 2. Three states, never two — plus a refusal that is not a state

| State | Count | Meaning | Source |
| --- | --- | --- | --- |
| `registered-mandatory` | 3 | in the hub catalog with the mandatory flag and a mandatory-registry row in lockstep | `governance/modules/model.py` |
| `target-pending` | 3 | declared as a target, not yet in the hub catalog: `shipped: false`, blocking hub issue named | `governance/modules/model.py` |
| `catalog-module-not-mandatory` | 3 | in the hub catalog, outside the mandatory set | `governance/modules/model.py` |
| `not-a-module` (refused, not a state) | 6 | a name the hub catalog does not carry: membership is refused, never inferred | `governance/modules/model.py` |

## 3. The module set

15 names resolve: 3 registered mandatory, 3 target-pending, 3 catalog modules outside the mandatory set, and 6 refused.

### 3.1 Registered mandatory — shipped to every repo

#### `code-indexing` — registered-mandatory

| Field | Value | Source |
| --- | --- | --- |
| owning repo | kushin77/code-indexing | `registry:code-indexing`, `vendor/CMR/catalog/modules/code-indexing/module.json` |
| mandatory status | `registered-mandatory` — in the hub catalog with the mandatory flag (shipped) (`mandatory: True`, `shipped: True`) | `registry:code-indexing`, `vendor/CMR/catalog/modules/code-indexing/module.json` |
| pin | v0.1.0 | `registry:code-indexing`, `vendor/CMR/catalog/modules/code-indexing/module.json` |
| rev | b6c49aa03992dba9fe4b87b46104b8fc2f69f224 | `registry:code-indexing`, `vendor/CMR/catalog/modules/code-indexing/module.json` |
| consumer assets | `.mcp.json`, `gdc-manifest.yaml` | `registry:code-indexing`, `vendor/CMR/catalog/modules/code-indexing/module.json` |
| health | not-run — deterministic offline build: the pin probe runs only with --probe | `governance/modules/health.py` |
| board ref | CMR:ONBOARD-0002 (#73) (cited, not resolved here) | `registry:code-indexing`, `vendor/CMR/catalog/modules/code-indexing/module.json` |
| drift | none | `registry:code-indexing`, `vendor/CMR/catalog/modules/code-indexing/module.json` |

| Consumer asset | Seed it comes from | Seed present | Source |
| --- | --- | --- | --- |
| .mcp.json | `templates/module/.mcp.json` | yes | templates/module/.mcp.json, registry:code-indexing |
| gdc-manifest.yaml | `templates/module/gdc-manifest.yaml` | yes | templates/module/gdc-manifest.yaml, registry:code-indexing |

#### `diagrams` — registered-mandatory

| Field | Value | Source |
| --- | --- | --- |
| owning repo | kushin77/diagrams | `registry:diagrams`, `vendor/CMR/catalog/modules/diagrams/module.json` |
| mandatory status | `registered-mandatory` — in the hub catalog with the mandatory flag (shipped) (`mandatory: True`, `shipped: True`) | `registry:diagrams`, `vendor/CMR/catalog/modules/diagrams/module.json` |
| pin | v0.1.0 | `registry:diagrams`, `vendor/CMR/catalog/modules/diagrams/module.json` |
| rev | b6c49aa03992dba9fe4b87b46104b8fc2f69f224 | `registry:diagrams`, `vendor/CMR/catalog/modules/diagrams/module.json` |
| consumer assets | `architecture.yaml`, `gdc-manifest.yaml` | `registry:diagrams`, `vendor/CMR/catalog/modules/diagrams/module.json` |
| health | not-run — deterministic offline build: the pin probe runs only with --probe | `governance/modules/health.py` |
| board ref | CMR:ONBOARD-0005 (#243) (cited, not resolved here) | `registry:diagrams`, `vendor/CMR/catalog/modules/diagrams/module.json` |
| drift | none | `registry:diagrams`, `vendor/CMR/catalog/modules/diagrams/module.json` |

| Consumer asset | Seed it comes from | Seed present | Source |
| --- | --- | --- | --- |
| architecture.yaml | `templates/module/architecture.yaml` | yes | templates/module/architecture.yaml, registry:diagrams |
| gdc-manifest.yaml | `templates/module/gdc-manifest.yaml` | yes | templates/module/gdc-manifest.yaml, registry:diagrams |

#### `shared-frontend` — registered-mandatory

| Field | Value | Source |
| --- | --- | --- |
| owning repo | kushin77/shared-frontend | `registry:shared-frontend`, `vendor/CMR/catalog/modules/shared-frontend/module.json` |
| mandatory status | `registered-mandatory` — in the hub catalog with the mandatory flag (shipped) (`mandatory: True`, `shipped: True`) | `registry:shared-frontend`, `vendor/CMR/catalog/modules/shared-frontend/module.json` |
| pin | v0.2.0 | `registry:shared-frontend`, `vendor/CMR/catalog/modules/shared-frontend/module.json` |
| rev | b6c49aa03992dba9fe4b87b46104b8fc2f69f224 | `registry:shared-frontend`, `vendor/CMR/catalog/modules/shared-frontend/module.json` |
| consumer assets | `gdc-manifest.yaml`, `tokens.json` | `registry:shared-frontend`, `vendor/CMR/catalog/modules/shared-frontend/module.json` |
| health | not-run — deterministic offline build: the pin probe runs only with --probe | `governance/modules/health.py` |
| board ref | CMR:ONBOARD-0003 (#163) (cited, not resolved here) | `registry:shared-frontend`, `vendor/CMR/catalog/modules/shared-frontend/module.json` |
| drift | none | `registry:shared-frontend`, `vendor/CMR/catalog/modules/shared-frontend/module.json` |

| Consumer asset | Seed it comes from | Seed present | Source |
| --- | --- | --- | --- |
| gdc-manifest.yaml | `templates/module/gdc-manifest.yaml` | yes | templates/module/gdc-manifest.yaml, registry:shared-frontend |
| tokens.json | `templates/module/tokens.json` | yes | templates/module/tokens.json, registry:shared-frontend |

### 3.2 Declared targets — pending, never rendered as shipped

#### `pmo` — target-pending

| Field | Value | Source |
| --- | --- | --- |
| owning repo | kushin77/pmo | `registry:pmo`, `governance/modules/targets.json` |
| mandatory status | `target-pending` — declared target, **not shipped** (`shipped: false`) (`mandatory: None`, `shipped: False`) | `registry:pmo`, `governance/modules/targets.json` |
| pin | — | `registry:pmo`, `governance/modules/targets.json` |
| rev | unavailable at this revision (the hub is not a git checkout) | `registry:pmo`, `governance/modules/targets.json`, `governance/modules/hub.py` |
| consumer assets | — | `registry:pmo`, `governance/modules/targets.json` |
| health | absent — the hub catalog carries no such entry at this revision | `governance/modules/health.py` |
| board ref | kushin77/CMR#435 (cited, not resolved here) | `registry:pmo`, `governance/modules/targets.json` |
| blocking hub issue(s) | `kushin77/CMR#435`, `kushin77/pmo#1` | `governance/modules/targets.json` |
| onboarding | CMR:ONBOARD-0009 | `governance/modules/targets.json` |
| drift | none | `registry:pmo`, `governance/modules/targets.json` |

| Consumer asset | Seed it comes from | Seed present | Source |
| --- | --- | --- | --- |
| (none) | — | — | registry:pmo |

#### `shared-governance` — target-pending

| Field | Value | Source |
| --- | --- | --- |
| owning repo | kushin77/shared-governance | `registry:shared-governance`, `governance/modules/targets.json` |
| mandatory status | `target-pending` — declared target, **not shipped** (`shipped: false`) (`mandatory: None`, `shipped: False`) | `registry:shared-governance`, `governance/modules/targets.json` |
| pin | — | `registry:shared-governance`, `governance/modules/targets.json` |
| rev | unavailable at this revision (the hub is not a git checkout) | `registry:shared-governance`, `governance/modules/targets.json`, `governance/modules/hub.py` |
| consumer assets | — | `registry:shared-governance`, `governance/modules/targets.json` |
| health | absent — the hub catalog carries no such entry at this revision | `governance/modules/health.py` |
| board ref | kushin77/CMR#264 (cited, not resolved here) | `registry:shared-governance`, `governance/modules/targets.json` |
| blocking hub issue(s) | `kushin77/CMR#264`, `kushin77/shared-governance#727` | `governance/modules/targets.json` |
| onboarding | CMR:ONBOARD-0008 | `governance/modules/targets.json` |
| drift | none | `registry:shared-governance`, `governance/modules/targets.json` |

| Consumer asset | Seed it comes from | Seed present | Source |
| --- | --- | --- | --- |
| (none) | — | — | registry:shared-governance |

#### `shared-services` — target-pending

| Field | Value | Source |
| --- | --- | --- |
| owning repo | kushin77/shared-services | `registry:shared-services`, `governance/modules/targets.json` |
| mandatory status | `target-pending` — declared target, **not shipped** (`shipped: false`) (`mandatory: None`, `shipped: False`) | `registry:shared-services`, `governance/modules/targets.json` |
| pin | — | `registry:shared-services`, `governance/modules/targets.json` |
| rev | unavailable at this revision (the hub is not a git checkout) | `registry:shared-services`, `governance/modules/targets.json`, `governance/modules/hub.py` |
| consumer assets | — | `registry:shared-services`, `governance/modules/targets.json` |
| health | absent — the hub catalog carries no such entry at this revision | `governance/modules/health.py` |
| board ref | kushin77/CMR#442 (cited, not resolved here) | `registry:shared-services`, `governance/modules/targets.json` |
| blocking hub issue(s) | `kushin77/CMR#442`, `kushin77/shared-services#4040` | `governance/modules/targets.json` |
| onboarding | CMR:ONBOARD-0010 | `governance/modules/targets.json` |
| drift | none | `registry:shared-services`, `governance/modules/targets.json` |

| Consumer asset | Seed it comes from | Seed present | Source |
| --- | --- | --- | --- |
| (none) | — | — | registry:shared-services |

### 3.3 Catalog modules outside the mandatory set

#### `erp-crm` — catalog-module-not-mandatory

| Field | Value | Source |
| --- | --- | --- |
| owning repo | kushin77/ERP-CRM | `registry:erp-crm`, `vendor/CMR/catalog/modules/erp-crm/module.json` |
| mandatory status | `catalog-module-not-mandatory` — in the hub catalog, outside the mandatory set (shipped) (`mandatory: False`, `shipped: True`) | `registry:erp-crm`, `vendor/CMR/catalog/modules/erp-crm/module.json` |
| pin | — | `registry:erp-crm`, `vendor/CMR/catalog/modules/erp-crm/module.json` |
| rev | b6c49aa03992dba9fe4b87b46104b8fc2f69f224 | `registry:erp-crm`, `vendor/CMR/catalog/modules/erp-crm/module.json` |
| consumer assets | — | `registry:erp-crm`, `vendor/CMR/catalog/modules/erp-crm/module.json` |
| health | not-run — deterministic offline build: the pin probe runs only with --probe | `governance/modules/health.py` |
| board ref | — (cited, not resolved here) | `registry:erp-crm`, `vendor/CMR/catalog/modules/erp-crm/module.json` |
| drift | none | `registry:erp-crm`, `vendor/CMR/catalog/modules/erp-crm/module.json` |

| Consumer asset | Seed it comes from | Seed present | Source |
| --- | --- | --- | --- |
| (none) | — | — | registry:erp-crm |

#### `googleworkspace` — catalog-module-not-mandatory

| Field | Value | Source |
| --- | --- | --- |
| owning repo | kushin77/googleworkspace | `registry:googleworkspace`, `vendor/CMR/catalog/modules/googleworkspace/module.json` |
| mandatory status | `catalog-module-not-mandatory` — in the hub catalog, outside the mandatory set (shipped) (`mandatory: False`, `shipped: True`) | `registry:googleworkspace`, `vendor/CMR/catalog/modules/googleworkspace/module.json` |
| pin | v0.3.0 | `registry:googleworkspace`, `vendor/CMR/catalog/modules/googleworkspace/module.json` |
| rev | b6c49aa03992dba9fe4b87b46104b8fc2f69f224 | `registry:googleworkspace`, `vendor/CMR/catalog/modules/googleworkspace/module.json` |
| consumer assets | — | `registry:googleworkspace`, `vendor/CMR/catalog/modules/googleworkspace/module.json` |
| health | not-run — deterministic offline build: the pin probe runs only with --probe | `governance/modules/health.py` |
| board ref | CMR:ONBOARD-0001 (#71) (cited, not resolved here) | `registry:googleworkspace`, `vendor/CMR/catalog/modules/googleworkspace/module.json` |
| drift | none | `registry:googleworkspace`, `vendor/CMR/catalog/modules/googleworkspace/module.json` |

| Consumer asset | Seed it comes from | Seed present | Source |
| --- | --- | --- | --- |
| (none) | — | — | registry:googleworkspace |

#### `saas-rbac` — catalog-module-not-mandatory

| Field | Value | Source |
| --- | --- | --- |
| owning repo | kushin77/saas-rbac | `registry:saas-rbac`, `vendor/CMR/catalog/modules/saas-rbac/module.json` |
| mandatory status | `catalog-module-not-mandatory` — in the hub catalog, outside the mandatory set (shipped) (`mandatory: False`, `shipped: True`) | `registry:saas-rbac`, `vendor/CMR/catalog/modules/saas-rbac/module.json` |
| pin | v2.4.0 | `registry:saas-rbac`, `vendor/CMR/catalog/modules/saas-rbac/module.json` |
| rev | b6c49aa03992dba9fe4b87b46104b8fc2f69f224 | `registry:saas-rbac`, `vendor/CMR/catalog/modules/saas-rbac/module.json` |
| consumer assets | — | `registry:saas-rbac`, `vendor/CMR/catalog/modules/saas-rbac/module.json` |
| health | not-run — deterministic offline build: the pin probe runs only with --probe | `governance/modules/health.py` |
| board ref | — (cited, not resolved here) | `registry:saas-rbac`, `vendor/CMR/catalog/modules/saas-rbac/module.json` |
| drift | none | `registry:saas-rbac`, `vendor/CMR/catalog/modules/saas-rbac/module.json` |

| Consumer asset | Seed it comes from | Seed present | Source |
| --- | --- | --- | --- |
| (none) | — | — | registry:saas-rbac |


## 4. Membership refused — not a state, and not a module

| Name | Recorded as | Why | Source |
| --- | --- | --- | --- |
| deepseek | register-claim | the hub catalog carries no module with this id — portfolio membership is not module membership (kushin77/CMR#952); the local admission register records admission='requested', which does not confer membership | `vendor/CMR/catalog/modules/deepseek` |
| gcp-gatekeeper | watch-question | the hub catalog carries no module with this id — portfolio membership is not module membership (kushin77/CMR#952); recorded as 'admission-undecided' at the hub | `vendor/CMR/catalog/modules/gcp-gatekeeper` |
| hermes-agents | register-claim | the hub catalog carries no module with this id — portfolio membership is not module membership (kushin77/CMR#952); the local admission register records admission='requested', which does not confer membership | `vendor/CMR/catalog/modules/hermes-agents` |
| monitoring-stack | watch-question | the hub catalog carries no module with this id — portfolio membership is not module membership (kushin77/CMR#952); recorded as 'becoming-a-module' at the hub | `vendor/CMR/catalog/modules/monitoring-stack` |
| ollama | register-claim | the hub catalog carries no module with this id — portfolio membership is not module membership (kushin77/CMR#952); the local admission register records admission='requested', which does not confer membership | `vendor/CMR/catalog/modules/ollama` |
| paperclip | register-claim | the hub catalog carries no module with this id — portfolio membership is not module membership (kushin77/CMR#952); the local admission register records admission='independent', which does not confer membership | `vendor/CMR/catalog/modules/paperclip` |

## 5. Distribution — the existing channel, unchanged

The brief, not a copy, travels with the assets: distribution stays with the hub's `controller/standards-sync.sh` and `controller/standards-manifest.txt`, whose seeds live under `templates/module/`. This lane produces the organized statement; it adds no second push mechanism and vendors nothing (GR-10 / ADR-0013 / NG4).

## 6. Findings

none — every claim above resolves to a registry row or a cited hub path.

