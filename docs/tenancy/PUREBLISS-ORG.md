# purebliss single-tenant org: CEO/CTO/CFO/PMO offices

Owner directive: the control plane's tenant is `purebliss` (purebliss.app /
ai.purebliss.app). Inside it, a governed organization is declared as four
**offices** — CEO, CTO, CFO, PMO — each owning its agents (profiles),
personas, system prompts, budgets and fleet assignment. Everything is
declarative IaC-style config: no human hand is needed at runtime to stand the
org up or keep it consistent.

## Source of truth, file by file

| Concern | File |
| --- | --- |
| Tenant org chart (roles, reporting edges, tier/cap/heartbeat) | `registry/personas/org-charts/purebliss.yaml` |
| Office layer (head, members, agents, prompts, budget, fleet) | `registry/personas/offices.yaml` |
| Office schema | `registry/personas/persona-offices.schema.json` |
| Tenant-scoped persona cards (shadow platform cards) | `registry/personas/cards/purebliss/{ceo,cto,cfo,pmo}.yaml` |
| Agent team pack (5 governed agents) | `registry/packs/releases/purebliss-team.1.0.0.yaml` |
| Routing group pinning agents to providers | `gateway/proxy/config/routing.yaml` (`routingGroups.purebliss-team`) |
| Tenant + role budget caps | `gateway/finops/budgets.yaml` (`tenant-purebliss`); caps themselves live only in the org chart |
| Daily token budget | `telemetry/metering/config/budgets.yaml` (`tenantId: purebliss`) |
| Soft/hard resource quotas | `telemetry/budgets/config/quotas.yaml` (`tenantId: purebliss`) |
| Per-tier provider routing override | `gateway/providers/example-tenant-overrides.yaml` (`tenantId: purebliss`) |
| Owner identity binding | `identity/chat/identity-map.json` (`kushin77@gmail.com` -> tenant `purebliss`) |
| Portal tenant roll-up | `portal/static/assets/fleet-hierarchy.json` |

## The four offices

Each office in `registry/personas/offices.yaml` declares, in one place:

- `headPersona` — the office head, a role node in
  `registry/personas/org-charts/purebliss.yaml` (CEO/CTO/CFO/PMO).
- `memberPersonas` — the SME/specialist personas that report into it (per
  `persona-card.schema.json`'s `reportsTo` enum). CTO owns the platform
  engineering bench (architecture, IaC, security, platform, frontend, QA,
  sync, debugging, mechanical); PMO owns `pmo-sme` and `docs-sme`.
- `agentProfiles` — which of the five `purebliss-team` pack agents
  (`ollama`, `paperclip`, `hermes`, `deepseek`, `claude`) the office
  dispatches through: `claude` for CEO/CTO orchestration and senior work,
  `deepseek` for CFO analysis, `hermes`/`ollama` as CTO's coding/local
  workers, `paperclip` for PMO's docs authoring.
- `promptRefs` — the office's system prompt module references
  (`<module>/<name>@v<n>`, matching each persona card's `systemPromptRef`).
- `monthlyBudgetUsd` — always equal to the head persona's cap in the org
  chart. This is a **verified restatement**, never a second literal: it is
  checked by `registry/personas/tests/test_purebliss_org.py`.
- `fleetPack` / `routingGroup` — both pinned to `purebliss-team@1.0.0` /
  `purebliss-team`, the one agent team and routing group this tenant runs.

## PMO is new

The platform org chart (`registry/personas/org-chart.yaml`) only ever had
`pmo-sme` — a specialist that coordinates program work but was never a
C-suite seat. This change adds a genuine `pmo` head-of-office persona
(`registry/personas/cards/purebliss/pmo.yaml`, MED tier, $100/mo,
every-15m heartbeat, reports to `ceo`) and adds `pmo` to the `reportsTo`
closed vocabulary in `persona-card.schema.json` so other personas (starting
with `pmo-sme` and `docs-sme`) can report into it. `pmo-sme` itself is
unchanged and remains a member of the PMO office, not its head.

## How to add an office or persona (IaC only, no runtime hand-editing)

1. Add or edit a role in `registry/personas/org-charts/purebliss.yaml` (tier,
   cap, heartbeat, `reportsTo`).
2. Add the matching persona card under `registry/personas/cards/purebliss/`
   with **identical** tier/cap/heartbeat/`reportsTo` — `registry.py`'s
   `validate_org_chart()` fails closed on any drift.
3. Add or edit the office entry in `registry/personas/offices.yaml`,
   including the member personas, agent profiles (must exist in the
   `purebliss-team` pack), prompt refs, and `monthlyBudgetUsd` (must equal
   the head persona's org-chart cap).
4. Run `python3 -m pytest registry/personas/tests` (covered by `make verify`
   via the `pytest-registry-personas` gate) — it revalidates the org chart,
   the offices schema, and every cross-reference (persona resolution, pack
   membership, routing group).
5. Never hand-edit `portal/static/assets/*.json` roll-ups independently of
   these seeds; they are projections, not a second source of truth.

## Office directory modules (integrated with parallel CTO/CFO lanes)

Two sibling lanes independently added per-office directory contracts under
`registry/personas/offices/<office>/` (`charter.yaml`, `abilities.yaml`,
`reports.yaml`, plus office-specific extras): CTO (issue #1573, PR #1579)
and CFO (issue #1582, PR #1583). This lane reconciled them into one shape:

- The canonical structural schema is
  `registry/personas/offices/schema/office.schema.json` — CTO's originally
  per-office schema (`cto/schema/office.schema.json`), moved to the shared
  location and generalized (`office` widened from `const: "cto"` to
  `enum: [ceo, cto, cfo, pmo]`; `schema` widened to a pattern covering all
  four offices).
- `registry/personas/offices/{ceo,pmo}/` were added in the same shape
  (charter/abilities/reports/prompts) so all four offices are consistent.
- CFO's `charter.yaml` predates the canonicalization and uses an
  incompatible field shape (`schemaVersion`/`tenant`/`personaCardRef`/
  `authority.{grants,denies}` instead of `schema`+`version`/
  `scopeOfAuthority`/`mayApprove`/`mayNotApprove`). It is NOT force-fitted
  onto the canonical schema (that would silently accept a shape the schema
  doesn't actually check); it keeps validating against its own schema,
  moved to `registry/personas/offices/schema/office-charter-legacy-cfo.schema.json`.
  `registry/personas/tests/test_office_modules.py` asserts this delta
  explicitly (a no-false-green proof: CFO's charter MUST fail the canonical
  schema). Reshaping CFO onto the canonical schema is a follow-up.
- `registry/personas/offices.yaml` now carries a `moduleDir` field per
  office pointing at its directory module; `registry/personas/persona-
  offices.schema.json` requires it.
- `gateway/finops/budgets.yaml`'s `tenant-purebliss` monthly figure ($750)
  is flagged as a proposal pending CFO office sign-off, not yet a reviewed
  number — the CFO lane's own cost model
  (`docs/cfo/COST-MODEL-2026-09-20.md`) had not covered a `purebliss` tenant
  entry as of this integration.

## Known deferral: demo tenants in `portal/server/state.py`

`portal/static/assets/fleet-hierarchy.json` still lists the pilot demo
tenants (`acme`, `globex`, `initech`) alongside `purebliss`. Fully removing
them touches `portal/server/state.py`'s seeded roster/org-bindings/audit
fixtures and `portal/tests/test_fleet_dashboard_dom.py`, which asserts a
three-tenant roll-up and mints its super-admin auth token against tenant
`acme` — a change with a much larger, separate blast radius than this
tenant-org lane. Tracked as follow-up work; this lane adds `purebliss` as a
first-class, fully governed tenant without deleting the demo fixtures other
gates still depend on.
