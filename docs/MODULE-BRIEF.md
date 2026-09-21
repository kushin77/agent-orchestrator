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
The hub revision is 33d04e5b6e35ad9a82d7ace9e10c5597fd21238d (hub-checkout); the board snapshot carries this repository's own board only, so a hub issue named below is **cited, never asserted** open or closed.

## 2. Three states, never two — plus a refusal that is not a state

| State | Count | Meaning | Source |
| --- | --- | --- | --- |
| `registered-mandatory` | 5 | in the hub catalog with the mandatory flag and a mandatory-registry row in lockstep | `governance/modules/model.py` |
| `target-pending` | 0 | declared as a target, not yet in the hub catalog: `shipped: false`, blocking hub issue named | `governance/modules/model.py` |
| `catalog-module-not-mandatory` | 7 | in the hub catalog, outside the mandatory set | `governance/modules/model.py` |
| `not-a-module` (refused, not a state) | 5 | a name the hub catalog does not carry: membership is refused, never inferred | `governance/modules/model.py` |

## 3. The module set

17 names resolve: 5 registered mandatory, 0 target-pending, 7 catalog modules outside the mandatory set, and 5 refused.

### 3.1 Registered mandatory — shipped to every repo

#### `code-indexing` — registered-mandatory

| Field | Value | Source |
| --- | --- | --- |
| owning repo | kushin77/code-indexing | `registry:code-indexing`, `vendor/CMR/catalog/modules/code-indexing/module.json` |
| mandatory status | `registered-mandatory` — in the hub catalog with the mandatory flag (shipped) (`mandatory: True`, `shipped: True`) | `registry:code-indexing`, `vendor/CMR/catalog/modules/code-indexing/module.json` |
| pin | v0.2.0 | `registry:code-indexing`, `vendor/CMR/catalog/modules/code-indexing/module.json` |
| rev | 33d04e5b6e35ad9a82d7ace9e10c5597fd21238d | `registry:code-indexing`, `vendor/CMR/catalog/modules/code-indexing/module.json` |
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
| rev | 33d04e5b6e35ad9a82d7ace9e10c5597fd21238d | `registry:diagrams`, `vendor/CMR/catalog/modules/diagrams/module.json` |
| consumer assets | `architecture.yaml`, `gdc-manifest.yaml` | `registry:diagrams`, `vendor/CMR/catalog/modules/diagrams/module.json` |
| health | not-run — deterministic offline build: the pin probe runs only with --probe | `governance/modules/health.py` |
| board ref | CMR:ONBOARD-0005 (#243) (cited, not resolved here) | `registry:diagrams`, `vendor/CMR/catalog/modules/diagrams/module.json` |
| drift | none | `registry:diagrams`, `vendor/CMR/catalog/modules/diagrams/module.json` |

| Consumer asset | Seed it comes from | Seed present | Source |
| --- | --- | --- | --- |
| architecture.yaml | `templates/module/architecture.yaml` | yes | templates/module/architecture.yaml, registry:diagrams |
| gdc-manifest.yaml | `templates/module/gdc-manifest.yaml` | yes | templates/module/gdc-manifest.yaml, registry:diagrams |

#### `pmo` — registered-mandatory

| Field | Value | Source |
| --- | --- | --- |
| owning repo | kushin77/pmo | `registry:pmo`, `vendor/CMR/catalog/modules/pmo/module.json` |
| mandatory status | `registered-mandatory` — in the hub catalog with the mandatory flag (shipped) (`mandatory: True`, `shipped: True`) | `registry:pmo`, `vendor/CMR/catalog/modules/pmo/module.json` |
| pin | v0.1.0 | `registry:pmo`, `vendor/CMR/catalog/modules/pmo/module.json` |
| rev | 33d04e5b6e35ad9a82d7ace9e10c5597fd21238d | `registry:pmo`, `vendor/CMR/catalog/modules/pmo/module.json` |
| consumer assets | `pmo-pin.yaml` | `registry:pmo`, `vendor/CMR/catalog/modules/pmo/module.json` |
| health | not-run — deterministic offline build: the pin probe runs only with --probe | `governance/modules/health.py` |
| board ref | ADR-0038 (cited, not resolved here) | `registry:pmo`, `vendor/CMR/catalog/modules/pmo/module.json` |
| drift | none | `registry:pmo`, `vendor/CMR/catalog/modules/pmo/module.json` |

| Consumer asset | Seed it comes from | Seed present | Source |
| --- | --- | --- | --- |
| pmo-pin.yaml | `templates/module/pmo-pin.yaml` | yes | templates/module/pmo-pin.yaml, registry:pmo |

#### `shared-frontend` — registered-mandatory

| Field | Value | Source |
| --- | --- | --- |
| owning repo | kushin77/shared-frontend | `registry:shared-frontend`, `vendor/CMR/catalog/modules/shared-frontend/module.json` |
| mandatory status | `registered-mandatory` — in the hub catalog with the mandatory flag (shipped) (`mandatory: True`, `shipped: True`) | `registry:shared-frontend`, `vendor/CMR/catalog/modules/shared-frontend/module.json` |
| pin | v0.2.0 | `registry:shared-frontend`, `vendor/CMR/catalog/modules/shared-frontend/module.json` |
| rev | 33d04e5b6e35ad9a82d7ace9e10c5597fd21238d | `registry:shared-frontend`, `vendor/CMR/catalog/modules/shared-frontend/module.json` |
| consumer assets | `gdc-manifest.yaml`, `tokens.json` | `registry:shared-frontend`, `vendor/CMR/catalog/modules/shared-frontend/module.json` |
| health | not-run — deterministic offline build: the pin probe runs only with --probe | `governance/modules/health.py` |
| board ref | CMR:ONBOARD-0003 (#163) (cited, not resolved here) | `registry:shared-frontend`, `vendor/CMR/catalog/modules/shared-frontend/module.json` |
| drift | none | `registry:shared-frontend`, `vendor/CMR/catalog/modules/shared-frontend/module.json` |

| Consumer asset | Seed it comes from | Seed present | Source |
| --- | --- | --- | --- |
| gdc-manifest.yaml | `templates/module/gdc-manifest.yaml` | yes | templates/module/gdc-manifest.yaml, registry:shared-frontend |
| tokens.json | `templates/module/tokens.json` | yes | templates/module/tokens.json, registry:shared-frontend |

#### `shared-governance` — registered-mandatory

| Field | Value | Source |
| --- | --- | --- |
| owning repo | kushin77/shared-governance | `registry:shared-governance`, `vendor/CMR/catalog/modules/shared-governance/module.json` |
| mandatory status | `registered-mandatory` — in the hub catalog with the mandatory flag (shipped) (`mandatory: True`, `shipped: True`) | `registry:shared-governance`, `vendor/CMR/catalog/modules/shared-governance/module.json` |
| pin | v0.1.0 | `registry:shared-governance`, `vendor/CMR/catalog/modules/shared-governance/module.json` |
| rev | 33d04e5b6e35ad9a82d7ace9e10c5597fd21238d | `registry:shared-governance`, `vendor/CMR/catalog/modules/shared-governance/module.json` |
| consumer assets | `gdc-manifest.yaml`, `governance-pin.yaml` | `registry:shared-governance`, `vendor/CMR/catalog/modules/shared-governance/module.json` |
| health | not-run — deterministic offline build: the pin probe runs only with --probe | `governance/modules/health.py` |
| board ref | CMR:ONBOARD-0008 (kushin77/shared-governance#727), catalog-request #632, DR-011 (#264) (cited, not resolved here) | `registry:shared-governance`, `vendor/CMR/catalog/modules/shared-governance/module.json` |
| drift | none | `registry:shared-governance`, `vendor/CMR/catalog/modules/shared-governance/module.json` |

| Consumer asset | Seed it comes from | Seed present | Source |
| --- | --- | --- | --- |
| gdc-manifest.yaml | `templates/module/gdc-manifest.yaml` | yes | templates/module/gdc-manifest.yaml, registry:shared-governance |
| governance-pin.yaml | `templates/module/governance-pin.yaml` | yes | templates/module/governance-pin.yaml, registry:shared-governance |

### 3.2 Declared targets — pending, never rendered as shipped

none.

### 3.3 Catalog modules outside the mandatory set

#### `agent-orchestrator` — catalog-module-not-mandatory

| Field | Value | Source |
| --- | --- | --- |
| owning repo | kushin77/agent-orchestrator | `registry:agent-orchestrator`, `vendor/CMR/catalog/modules/agent-orchestrator/module.json` |
| mandatory status | `catalog-module-not-mandatory` — in the hub catalog, outside the mandatory set (shipped) (`mandatory: False`, `shipped: True`) | `registry:agent-orchestrator`, `vendor/CMR/catalog/modules/agent-orchestrator/module.json` |
| pin | v0.1.0 | `registry:agent-orchestrator`, `vendor/CMR/catalog/modules/agent-orchestrator/module.json` |
| rev | 33d04e5b6e35ad9a82d7ace9e10c5597fd21238d | `registry:agent-orchestrator`, `vendor/CMR/catalog/modules/agent-orchestrator/module.json` |
| consumer assets | — | `registry:agent-orchestrator`, `vendor/CMR/catalog/modules/agent-orchestrator/module.json` |
| health | not-run — deterministic offline build: the pin probe runs only with --probe | `governance/modules/health.py` |
| board ref | CMR:ONBOARD-0012 (#554) (cited, not resolved here) | `registry:agent-orchestrator`, `vendor/CMR/catalog/modules/agent-orchestrator/module.json` |
| drift | none | `registry:agent-orchestrator`, `vendor/CMR/catalog/modules/agent-orchestrator/module.json` |

| Consumer asset | Seed it comes from | Seed present | Source |
| --- | --- | --- | --- |
| (none) | — | — | registry:agent-orchestrator |

#### `erp-crm` — catalog-module-not-mandatory

| Field | Value | Source |
| --- | --- | --- |
| owning repo | kushin77/ERP-CRM | `registry:erp-crm`, `vendor/CMR/catalog/modules/erp-crm/module.json` |
| mandatory status | `catalog-module-not-mandatory` — in the hub catalog, outside the mandatory set (shipped) (`mandatory: False`, `shipped: True`) | `registry:erp-crm`, `vendor/CMR/catalog/modules/erp-crm/module.json` |
| pin | — | `registry:erp-crm`, `vendor/CMR/catalog/modules/erp-crm/module.json` |
| rev | 33d04e5b6e35ad9a82d7ace9e10c5597fd21238d | `registry:erp-crm`, `vendor/CMR/catalog/modules/erp-crm/module.json` |
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
| rev | 33d04e5b6e35ad9a82d7ace9e10c5597fd21238d | `registry:googleworkspace`, `vendor/CMR/catalog/modules/googleworkspace/module.json` |
| consumer assets | — | `registry:googleworkspace`, `vendor/CMR/catalog/modules/googleworkspace/module.json` |
| health | not-run — deterministic offline build: the pin probe runs only with --probe | `governance/modules/health.py` |
| board ref | CMR:ONBOARD-0001 (#71) (cited, not resolved here) | `registry:googleworkspace`, `vendor/CMR/catalog/modules/googleworkspace/module.json` |
| drift | none | `registry:googleworkspace`, `vendor/CMR/catalog/modules/googleworkspace/module.json` |

| Consumer asset | Seed it comes from | Seed present | Source |
| --- | --- | --- | --- |
| (none) | — | — | registry:googleworkspace |

#### `monitoring-stack` — catalog-module-not-mandatory

| Field | Value | Source |
| --- | --- | --- |
| owning repo | kushin77/monitoring-stack | `registry:monitoring-stack`, `vendor/CMR/catalog/modules/monitoring-stack/module.json` |
| mandatory status | `catalog-module-not-mandatory` — in the hub catalog, outside the mandatory set (shipped) (`mandatory: False`, `shipped: True`) | `registry:monitoring-stack`, `vendor/CMR/catalog/modules/monitoring-stack/module.json` |
| pin | — | `registry:monitoring-stack`, `vendor/CMR/catalog/modules/monitoring-stack/module.json` |
| rev | 33d04e5b6e35ad9a82d7ace9e10c5597fd21238d | `registry:monitoring-stack`, `vendor/CMR/catalog/modules/monitoring-stack/module.json` |
| consumer assets | — | `registry:monitoring-stack`, `vendor/CMR/catalog/modules/monitoring-stack/module.json` |
| health | not-run — deterministic offline build: the pin probe runs only with --probe | `governance/modules/health.py` |
| board ref | ADR-0042 (cited, not resolved here) | `registry:monitoring-stack`, `vendor/CMR/catalog/modules/monitoring-stack/module.json` |
| drift | none | `registry:monitoring-stack`, `vendor/CMR/catalog/modules/monitoring-stack/module.json` |

| Consumer asset | Seed it comes from | Seed present | Source |
| --- | --- | --- | --- |
| (none) | — | — | registry:monitoring-stack |

#### `saas-rbac` — catalog-module-not-mandatory

| Field | Value | Source |
| --- | --- | --- |
| owning repo | kushin77/saas-rbac | `registry:saas-rbac`, `vendor/CMR/catalog/modules/saas-rbac/module.json` |
| mandatory status | `catalog-module-not-mandatory` — in the hub catalog, outside the mandatory set (shipped) (`mandatory: False`, `shipped: True`) | `registry:saas-rbac`, `vendor/CMR/catalog/modules/saas-rbac/module.json` |
| pin | v2.4.0 | `registry:saas-rbac`, `vendor/CMR/catalog/modules/saas-rbac/module.json` |
| rev | 33d04e5b6e35ad9a82d7ace9e10c5597fd21238d | `registry:saas-rbac`, `vendor/CMR/catalog/modules/saas-rbac/module.json` |
| consumer assets | — | `registry:saas-rbac`, `vendor/CMR/catalog/modules/saas-rbac/module.json` |
| health | not-run — deterministic offline build: the pin probe runs only with --probe | `governance/modules/health.py` |
| board ref | — (cited, not resolved here) | `registry:saas-rbac`, `vendor/CMR/catalog/modules/saas-rbac/module.json` |
| drift | none | `registry:saas-rbac`, `vendor/CMR/catalog/modules/saas-rbac/module.json` |

| Consumer asset | Seed it comes from | Seed present | Source |
| --- | --- | --- | --- |
| (none) | — | — | registry:saas-rbac |

#### `shared-services` — catalog-module-not-mandatory

| Field | Value | Source |
| --- | --- | --- |
| owning repo | kushin77/shared-services | `registry:shared-services`, `vendor/CMR/catalog/modules/shared-services/module.json` |
| mandatory status | `catalog-module-not-mandatory` — in the hub catalog, outside the mandatory set (shipped) (`mandatory: False`, `shipped: True`) | `registry:shared-services`, `vendor/CMR/catalog/modules/shared-services/module.json` |
| pin | — | `registry:shared-services`, `vendor/CMR/catalog/modules/shared-services/module.json` |
| rev | 33d04e5b6e35ad9a82d7ace9e10c5597fd21238d | `registry:shared-services`, `vendor/CMR/catalog/modules/shared-services/module.json` |
| consumer assets | — | `registry:shared-services`, `vendor/CMR/catalog/modules/shared-services/module.json` |
| health | not-run — deterministic offline build: the pin probe runs only with --probe | `governance/modules/health.py` |
| board ref | CMR:ONBOARD-0010 (cited, not resolved here) | `registry:shared-services`, `vendor/CMR/catalog/modules/shared-services/module.json` |
| drift | none | `registry:shared-services`, `vendor/CMR/catalog/modules/shared-services/module.json` |

| Consumer asset | Seed it comes from | Seed present | Source |
| --- | --- | --- | --- |
| (none) | — | — | registry:shared-services |

#### `twilio` — catalog-module-not-mandatory

| Field | Value | Source |
| --- | --- | --- |
| owning repo | kushin77/twilio | `registry:twilio`, `vendor/CMR/catalog/modules/twilio/module.json` |
| mandatory status | `catalog-module-not-mandatory` — in the hub catalog, outside the mandatory set (shipped) (`mandatory: False`, `shipped: True`) | `registry:twilio`, `vendor/CMR/catalog/modules/twilio/module.json` |
| pin | v0.1.0 | `registry:twilio`, `vendor/CMR/catalog/modules/twilio/module.json` |
| rev | 33d04e5b6e35ad9a82d7ace9e10c5597fd21238d | `registry:twilio`, `vendor/CMR/catalog/modules/twilio/module.json` |
| consumer assets | — | `registry:twilio`, `vendor/CMR/catalog/modules/twilio/module.json` |
| health | not-run — deterministic offline build: the pin probe runs only with --probe | `governance/modules/health.py` |
| board ref | — (cited, not resolved here) | `registry:twilio`, `vendor/CMR/catalog/modules/twilio/module.json` |
| drift | none | `registry:twilio`, `vendor/CMR/catalog/modules/twilio/module.json` |

| Consumer asset | Seed it comes from | Seed present | Source |
| --- | --- | --- | --- |
| (none) | — | — | registry:twilio |


## 4. Membership refused — not a state, and not a module

| Name | Recorded as | Why | Source |
| --- | --- | --- | --- |
| deepseek | register-claim | the hub catalog carries no module with this id — portfolio membership is not module membership (kushin77/CMR#952); the local admission register records admission='requested', which does not confer membership | `vendor/CMR/catalog/modules/deepseek` |
| gcp-gatekeeper | watch-question | the hub catalog carries no module with this id — portfolio membership is not module membership (kushin77/CMR#952); recorded as 'admission-undecided' at the hub | `vendor/CMR/catalog/modules/gcp-gatekeeper` |
| hermes-agents | register-claim | the hub catalog carries no module with this id — portfolio membership is not module membership (kushin77/CMR#952); the local admission register records admission='requested', which does not confer membership | `vendor/CMR/catalog/modules/hermes-agents` |
| ollama | register-claim | the hub catalog carries no module with this id — portfolio membership is not module membership (kushin77/CMR#952); the local admission register records admission='requested', which does not confer membership | `vendor/CMR/catalog/modules/ollama` |
| paperclip | register-claim | the hub catalog carries no module with this id — portfolio membership is not module membership (kushin77/CMR#952); the local admission register records admission='independent', which does not confer membership | `vendor/CMR/catalog/modules/paperclip` |

## 5. Distribution — the existing channel, unchanged

The brief, not a copy, travels with the assets: distribution stays with the hub's `controller/standards-sync.sh` and `controller/standards-manifest.txt`, whose seeds live under `templates/module/`. This lane produces the organized statement; it adds no second push mechanism and vendors nothing (GR-10 / ADR-0013 / NG4).

## 6. Findings

none — every claim above resolves to a registry row or a cited hub path.

