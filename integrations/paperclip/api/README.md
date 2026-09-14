# `integrations/paperclip/api/` — the paperclip HTTP surface projection

This subpackage closes **issue #413**: it projects the fleet's own state over the
upstream paperclip HTTP surface — `GET /api/health`, `GET /api/openapi.json`, the
company-scoped route shape `/api/companies/{companyId}/...` and the mapped error
taxonomy — so an operator reads **our** truth across the process boundary.

It is a subpackage of the canonical adapter module (`integrations/paperclip/`),
which is the only legitimate home for paperclip boundary code — the lane brief's
`paperclip/api/**` is refused by
[`scripts/check-paperclip-canonical-module.sh`](../../../scripts/check-paperclip-canonical-module.sh)
(issue #448/#457, [ADR-0016](../../../docs/decision-records/ADR-0016-paperclip-boundary-single-module.md)).
The integration mode is [ADR-0013](../../../docs/decision-records/ADR-0013-paperclip-ing-integration.md);
the seam is [`docs/PAPERCLIP-ING-INTEGRATION.md`](../../../docs/PAPERCLIP-ING-INTEGRATION.md).

**Transport is not this lane.** The serving layer is SPoG #339
(`portal/server/bridge.py`, `/api/v1/bridge*`); this package owns the
*paperclip-shaped projection* on top of it, not the transport.

## The files

| File | Role |
|---|---|
| `contracts.py` | emits the OpenAPI schema components **from the frozen contracts** (`docs/contracts/paperclip/*.schema.json`) and **introspects the adapter's own `to_dict` shapes** |
| `surface.py` | reads the route set from the adapter's own client (`../client.py`) by exercising each argument-free method against a recording transport |
| `taxonomy.py` | the seven-status error taxonomy, **mapped** from the seam's typed errors and the fleet's existing wire codes |
| `company.py` | the **ONE declared mapping** from `{companyId}` onto the fleet's tenancy, plus scope resolution |
| `health.py` | the real health read (claim ledger readable, ticket projection fresh) and the independent cross-check |
| `openapi.py` | assembles the document, serializes it deterministically, and refuses drift by name |
| `errors.py` | the surface's refusals, reusing the auth seam's `AuthError` shape and the fleet's own codes |
| `cli.py` | `emit` / `check` / `health` / `company` / `controls` |
| `openapi.json` | the committed, byte-reproducible emitted document |
| `tests/` | the offline suite |

## Acceptance criteria, mapped

| Criterion | Where it is answered |
|---|---|
| **The contracts are the source** — the document is emitted from the frozen schemas + the adapters' own shapes, never hand-written in parallel | `contracts.contract_components` embeds `docs/contracts/paperclip/{ticket,heartbeat,budget}.schema.json` verbatim (the ticket **v2** `facets`/`authority` fields ride along because they are in the schema); `contracts.adapter_components` builds a sample of each model type and derives the shape from its `to_dict()` output, recording `x-shape-provenance`. `surface.client_routes` reads the **routes** from `client.py` itself. A segment with no introspectable shape (`dashboard`) is declared **unshaped** by name, never invented |
| **Company scope is a projection of our tenancy** — one declared mapping, cross-company refused 403 | `company.py`: `{companyId}` **is** `identity/rbac.Org.id` == `identity/onboarding.Tenant.id` (the identity relation), declared once in `mapping()`. The known set is read from the fleet's own on-disk tenant declaration (`telemetry/budgets/config/policies.yaml`), never hand-listed. A cross-company read is `403 cross_tenant`; an undeclared company is `404 not_found`; an unreadable declaration fails closed to an empty set |
| **The error taxonomy is mapped, not invented** — 400/401/403/404/409/422/503, each with ≥1 test | `taxonomy.py` reads the status set from `integrations/paperclip/model.ERROR_BY_STATUS`, its descriptions from each error class's own docstring, and its wire codes by **calling the fleet's existing refusal constructors** (`.status`, `.code`). `tests/test_taxonomy.py` carries one test per status |
| **`/api/health` reflects real dependencies** and reports 503 rather than a green lie | `health.py` reads the claim ledger (`.board/claims.jsonl`, must be readable) and the board snapshot (`.board/snapshot.json`, `generated_at` within `governance/policy/lease.SNAPSHOT_STALENESS_MINUTES`). Missing/unreadable ⇒ `unhealthy`/503; stale ⇒ `degraded`. `check_report` recomputes from the probes and refuses a report that claims ok while a dependency is not |
| **The emitted document is deterministic** | `serialize` sorts keys and fixes indentation; `tests/test_determinism.py` asserts two builds are byte-identical and the committed artifact matches a fresh emission |

## The declared company mapping (verbatim)

```json
{
  "id": "ao.paperclip.company/v1",
  "upstream": "companyId",
  "relation": "identity",
  "target_authority": "identity/rbac.Org.id (== identity/onboarding.Tenant.id)",
  "declared_source": "telemetry/budgets/config/policies.yaml",
  "declared_source_pointer": "/policies/*/tenantId",
  "refusal": {
    "cross_company": {"status": 403, "code": "cross_tenant"},
    "unknown_company": {"status": 404, "code": "not_found"}
  }
}
```

## The refusals (each provoked by name)

| Control | Refused as |
|---|---|
| a document that drifts from the frozen contracts | `schema 'Ticket' drifts from the frozen contract docs/contracts/paperclip/ticket.schema.json` |
| a missing error-taxonomy entry | `the error taxonomy entry '422' is missing from components.responses` |
| an undeclared company mapping | `the company mapping is undeclared (x-company-mapping)` |
| health reporting ok while a dependency is absent | `health reports dependency 'claim_ledger' ok while it is missing` |

## Running it

```bash
bash scripts/check-paperclip-openapi.sh                  # the gate (tri-state)
bash scripts/check-paperclip-openapi.sh --no-controls    # structure only
python3 integrations/paperclip/api/cli.py check          # document vs sources vs artifact
python3 integrations/paperclip/api/cli.py controls       # provoke every refusal
python3 integrations/paperclip/api/cli.py health         # read the real dependencies
python3 integrations/paperclip/api/cli.py emit --out integrations/paperclip/api/openapi.json
```

The committed `openapi.json` is the emitted document; re-emit it after any change
to the contracts, the adapter shapes, the client's routes, the taxonomy or the
company mapping. The gate fails if it is stale or hand-edited.
