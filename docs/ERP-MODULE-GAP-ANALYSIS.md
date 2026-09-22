# ERP module gap analysis — frappe/erpnext feature surface vs our pillars

Issue: [#612](https://github.com/kushin77/agent-orchestrator/issues/612) ·
EPIC: [#645](https://github.com/kushin77/agent-orchestrator/issues/645) ·
Children: [#646](https://github.com/kushin77/agent-orchestrator/issues/646)–[#655](https://github.com/kushin77/agent-orchestrator/issues/655)

## Why this doc exists

Issue #612 orders an ERP capability in the control plane: use frappe/erpnext
as the starting point, cannibalize its end-to-end discipline for our
enterprise e2e, integrate it as a **complete mandatory module**, feed every
module datum **from the indexer**, and gap every upstream feature into
backend + frontend integration work tracked by an epic. This doc is that
gap, feature by feature, so each child lane builds against a measured delta
instead of re-deriving the upstream surface.

**Basis of this gap:** ERPNext public docs and feature pages
(`docs.frappe.io/erpnext`, `frappe.io/erpnext`, version-16 material) read
2026-09-14, plus this repo's own pillar docs
([`ARCHITECTURE.md`](ARCHITECTURE.md), [`EXECUTION-PLAN.md`](EXECUTION-PLAN.md),
[`CANNIBALIZATION.md`](CANNIBALIZATION.md), [`QA-GATE.md`](QA-GATE.md)) and the
knowledge-index contract ([`../governance/knowledge/README.md`](../governance/knowledge/README.md)).
No upstream clone was made and no upstream code is copied: ERPNext is
**GPL-3.0**, so upstream is a *pattern source only*. Every harvested shape
records provenance (GR-10) through the cannibalization index
([`CANNIBALIZATION.md`](CANNIBALIZATION.md)).

## What the control plane already has (the cannibalization base)

The module is an **assembly job**, not a greenfield ERP. Our side already
owns the hard parts, mapped by pillar in
[`ARCHITECTURE.md`](ARCHITECTURE.md):

| Surface | What it gives the ERP module |
|---|---|
| `integrations/paperclip/` ([README](../integrations/paperclip/README.md)) | The proven integration-module assembly: `api/` (OpenAPI surface), `auth/`, `reporting/`, in-module tests, manifest discipline |
| `governance/knowledge/` ([README](../governance/knowledge/README.md)) | **The indexer** — the single catalogue of institutional knowledge; module data must flow from it (`sources.py` is the one place to add a source) |
| `registry/**/*.schema.json` | Schema-authoring discipline (provenance, `$id`, validation) |
| `engine/core` | Durable state-machine execution; ERP workflows are *data* it consumes |
| `identity/rbac`, `guardrails/policy` | Tenant scoping and policy gates the module binds to |
| `telemetry/ledger`, `telemetry/metering` | Hash-chained audit and per-tenant usage roll-up |
| `portal/static/`, `portal/server/` | The admin UI shell the module's frontend mounts into |
| `infra/feature-flags/` | AO-GR-6: every new surface ships enabled by default |
| `e2e/` ([README](../e2e/README.md)) | The enterprise harness: golden path + no-false-green negative controls, fully offline |

## The module shape

`integrations/erp/` — declared in `module.yaml` as **mandatory**, flag
`erp-module` default **off**, `data_source: indexer`. Backend integration
(document model, transactions, workflows) lives under
`integrations/erp/{core,tx,ops,crm}/`, the REST surface under
`integrations/erp/api/`, scoping under `integrations/erp/auth/`, metering
under `integrations/erp/finops/`. Frontend integration mounts in the portal
behind the flag. The e2e stage lives in `e2e/erp/`.

## The gap — ERPNext feature → our pillar, feature by feature

Columns: **backend** (document model, workflows, transactions), **frontend**
(portal surfaces), **indexer** (data flow from `governance/knowledge/`),
**identity/RBAC**, **FinOps**. Call = cannibalize (use/port our own surface,
harvest upstream as pattern) or build (new).

| # | ERPNext feature | Backend gap | Frontend gap | Indexer gap | Identity/RBAC gap | FinOps gap | Call | Child |
|---|---|---|---|---|---|---|---|---|
| 1 | **Selling** — quotations, sales orders, delivery notes, sales invoices, POS | Document family schemas + state workflows (draft→submitted→completed/cancelled); closed selling→stock→accounting loop | Quotation/order/invoice lists, forms, reports | Document definitions and rules served from the indexer catalogue | Role-gated read/write per document type | Every transition metered on the ledger | Cannibalize `engine/core` workflow patterns + `registry` schema style; harvest ERPNext doctype semantics as patterns | [#646](https://github.com/kushin77/agent-orchestrator/issues/646), [#647](https://github.com/kushin77/agent-orchestrator/issues/647), [#648](https://github.com/kushin77/agent-orchestrator/issues/648) |
| 2 | **Buying / procurement** — suppliers, RFQs, purchase orders, receipts, invoices, subcontracting | Purchase cycle documents and posting rules; subcontracting notes | Purchase dashboards and supplier forms | Supplier/item catalogue indexer-fed | Approval roles for POs | Metered purchase operations | Same core model as selling; cannibalize ERPNext procurement semantics as patterns | [#647](https://github.com/kushin77/agent-orchestrator/issues/647), [#649](https://github.com/kushin77/agent-orchestrator/issues/649) |
| 3 | **Stock / inventory** — item master, warehouses, stock entries, serial/batch, reorder, valuation, reconciliation | Stock movement ledger, valuation methods, serial/batch tracking as data | Inventory lists, stock balance views, reorder alerts | Item/warehouse definitions from the indexer | Warehouse-level visibility scoping | Stock operations metered | Build on our deterministic event style; harvest ERPNext stock-valuation semantics | [#647](https://github.com/kushin77/agent-orchestrator/issues/647), [#648](https://github.com/kushin77/agent-orchestrator/issues/648) |
| 4 | **Accounting & finance** — GL, AR/AP, multi-currency, consolidation, financial statements, budgets, tax/localization | Double-entry GL posting model; multi-currency + multi-company consolidation as data | Trial balance, P&L, balance sheet, budget dashboards | Chart of accounts and posting rules indexer-fed | Financial-role segregation (maker/checker) | Ledger-aligned usage + cost roll-up | Harvest ERPNext double-entry semantics as patterns; cannibalize `telemetry/ledger` hash-chain discipline | [#648](https://github.com/kushin77/agent-orchestrator/issues/648), [#654](https://github.com/kushin77/agent-orchestrator/issues/654) |
| 5 | **Fixed assets** — lifecycle, depreciation schedules | Asset documents + depreciation schedule runner (data-driven) | Asset registers and depreciation reports | Asset category rules indexer-fed | Asset custodianship roles | Depreciation runs metered | Pattern-harvest only; schedule runner built on our workflow data | [#649](https://github.com/kushin77/agent-orchestrator/issues/649) |
| 6 | **Manufacturing** — BOM, work orders, production plans, capacity | BOM explosion, work-order state machine, stock consumption on completion | Production dashboards, work-order boards | BOM/routing definitions indexer-fed | Shop-floor role scoping | Production operations metered | Harvest ERPNext BOM/work-order semantics; build on core workflows | [#649](https://github.com/kushin77/agent-orchestrator/issues/649) |
| 7 | **CRM** — leads, opportunities, customers, campaigns | Lead→opportunity→customer state machine | Funnel and pipeline views | Lead-stage definitions indexer-fed | Sales-territory scoping | Pipeline events metered | Cannibalize portal leaderboard/table patterns; harvest upstream CRM semantics | [#650](https://github.com/kushin77/agent-orchestrator/issues/650) |
| 8 | **Projects** — projects, tasks, timesheets, costing | Project/task/timesheet documents with cost accumulation | Project boards and timesheets | Task-type definitions indexer-fed | Project-membership scoping | Timesheet entries metered | Same core-model discipline; pattern-harvest | [#650](https://github.com/kushin77/agent-orchestrator/issues/650) |
| 9 | **Quality** — inspections, quality actions | Inspection records with outcome transitions | Inspection lists and results | Inspection templates indexer-fed | Inspector role scoping | Inspection events metered | Pattern-harvest; build on workflow data | [#650](https://github.com/kushin77/agent-orchestrator/issues/650) |
| 10 | **Support / helpdesk** — issues, SLAs | Support issue documents with deterministic SLA ageing | Ticket queues and SLA boards | SLA policy definitions indexer-fed | Agent/tenant scoping | Support interactions metered | Cannibalize the fleet's event/beat patterns for SLA timers | [#650](https://github.com/kushin77/agent-orchestrator/issues/650) |
| 11 | **HR & payroll** — employees, attendance, leave, payroll | Deferred: upstream split HR into the separate Frappe HR app | — | — | — | — | Deferred (see Non-goals) | — |
| 12 | **Website / e-commerce** — web views, cart | Out of the module's first scope: our consumer surface is REST + SDK, not a public web store | — | — | — | — | Deferred (see Non-goals) | — |
| 13 | **Platform capabilities** — no-code forms, approval workflows, print formats, dashboards/reports, roles & permissions, REST API, mobile | Approval workflows become workflow data; REST API is the module surface | Dashboards/reports mount in the portal; print formats out of scope (no document rendering plane) | Form/workflow definitions indexer-fed | Roles & permissions → the module's auth layer | API usage metered per call | Cannibalize our workflow data + portal patterns; harvest upstream capability semantics | [#646](https://github.com/kushin77/agent-orchestrator/issues/646), [#651](https://github.com/kushin77/agent-orchestrator/issues/651), [#652](https://github.com/kushin77/agent-orchestrator/issues/652), [#653](https://github.com/kushin77/agent-orchestrator/issues/653) |
| 14 | **Localization / multi-company / multi-currency** | Multi-company + multi-currency as first-class document data; per-region rule packs deferred | Regional settings in portal | Regional rule definitions indexer-fed | Cross-company visibility policy | Cross-currency cost conversion in roll-up | Pattern-harvest; build the data model, defer region packs | [#648](https://github.com/kushin77/agent-orchestrator/issues/648) |

## Cannibalize vs build — summary

| Piece | Cannibalize (use as-is or port) | Build (new) |
|---|---|---|
| Module assembly | `integrations/paperclip/` — `api/`, `auth/`, reporting, manifest, test layout | `integrations/erp/` skeleton + `module.yaml` (mandatory, flag OFF, indexer-fed) |
| Data plane | `governance/knowledge/` — the indexer becomes the ERP module's single source of definitions | ERP catalogue sources + `sources.py` registration |
| Document model | `registry/**/*.schema.json` authoring discipline | ERP document-family schemas + validators |
| Workflows | `engine/core` durable state-machine (consumed as data) | ERP workflow definitions (draft→submitted→completed/cancelled) |
| API | `integrations/paperclip/api/` OpenAPI generation pattern | ERP REST surface over the schemas |
| Frontend | `portal/static/` shell, dashboard/table patterns | ERP module frames behind the `erp-module` flag |
| Scoping | `identity/rbac`, `guardrails/policy` as consumed contracts | In-module `auth/` role→permission map + field policies |
| Metering | `telemetry/ledger` + `telemetry/metering` public APIs | In-module `finops/` events, roll-up, budget hard-stop |
| E2E | `e2e/golden_path.py` stage discipline, `e2e/negative_controls.py` no-false-green style; **upstream ERPNext e2e scenario/fixture patterns** (patterns, never code) | `e2e/erp/` golden path + negative controls |

## Roadmap (the epic's children)

One issue = one lane = one branch = disjoint files (GR-3). Full acceptance
criteria and `Verify:` commands live on the board — not here.

| Child | Scope | Pillar | Depends on |
|---|---|---|---|
| [#646](https://github.com/kushin77/agent-orchestrator/issues/646) ERP-01 | Module skeleton, manifest, **indexer data contract** | governance | — |
| [#647](https://github.com/kushin77/agent-orchestrator/issues/647) ERP-02 | Core document model: schemas + workflow data | control-plane | — |
| [#648](https://github.com/kushin77/agent-orchestrator/issues/648) ERP-03 | Transactional spine: selling→stock→accounting | control-plane | #647 |
| [#649](https://github.com/kushin77/agent-orchestrator/issues/649) ERP-04 | Procurement + manufacturing (assets, BOM, work orders) | control-plane | #647 |
| [#650](https://github.com/kushin77/agent-orchestrator/issues/650) ERP-05 | CRM, projects, quality, support | control-plane | — |
| [#651](https://github.com/kushin77/agent-orchestrator/issues/651) ERP-06 | REST API + OpenAPI surface | control-plane | #647 |
| [#652](https://github.com/kushin77/agent-orchestrator/issues/652) ERP-07 | Portal frontend integration (flag-gated) | control-plane | #651 |
| [#653](https://github.com/kushin77/agent-orchestrator/issues/653) ERP-08 | Tenant RBAC + guardrail scoping | identity-rbac | #646 |
| [#654](https://github.com/kushin77/agent-orchestrator/issues/654) ERP-09 | FinOps metering + usage telemetry | observability-finops | #647 |
| [#655](https://github.com/kushin77/agent-orchestrator/issues/655) ERP-10 | Enterprise e2e (cannibalized patterns, mandatory stage) | control-plane | #651, #652, #653 |

## Non-goals / deferred

- **No ERPNext code is copied or vendored** — GPL-3.0 upstream, patterns
  only, provenance recorded (GR-10); `.research/` clones stay gitignored.
- **Frappe HR/Payroll** and **website/e-commerce** are separate upstream
  apps; they are dispositioned deferred, not silently dropped.
- **Vertical industry apps** (Education, Healthcare, Agriculture,
  Non-Profit, Hospitality) are out of the module's first scope; they arrive
  as their own direction issues if ever needed.
- **No console clicks or ad-hoc infra** — the module ships enabled by default
  (AO-GR-6), declared, never clicked.
- The module consumes `engine/`, `identity/`, `telemetry/`, `portal/`
  through their public contracts — child lanes never edit those pillar
  files except their own additive `portal/static/erp/**` + one
  `portal/server/` route module (ERP-07) and the suite registration line
  (ERP-10).
