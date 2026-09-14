---
id: ADR-0027
status: accepted
date: 2026-09-14
deciders: [owner]
req: []
supersedes: []
---

# ADR-0027: ERPNext as the financial system of record + localized AI inference

## Status

`accepted` — ratified on the PR for issue #669 (PF-4), child of EPIC #665
("ERPNext & DeepSeek FinOps: from manual reconciliation to a token-optimal,
ledger-faithful subscription engine").

**It supersedes no ADR.** No record ever fixed a financial system of record for
the control plane; the silo this decision closes was formed by accretion (money
and tokens flowing through disconnected surfaces with no declared ledger
authority), not by a prior decision. That is why the commitment has to be
written down at all: an unrecorded default is invisible.

**It consumes the Phase-1 context and re-decides none of it.** PF-1 (#666),
PF-2 (#667) and PF-3 (#668) *measured* the current state — the silos, the token
flows, the metric gaps. This record is the **decision** that resolves those
measurements; it does not restate their numbers.

### Numbering note (issue #669 named no number; re-derived at lane start)

Measured on this lane's base, `origin/master`, with the issue's own method — the
test is the highest ADR **file** present, not a number written in prose:

1. **Files.** `docs/decision-records/` on `origin/master` carries `ADR-0001`–
   `ADR-0018`, `ADR-0022`, `ADR-0025` and `ADR-0026`. `ADR-0019`–`ADR-0021` have
   no file by the index's own rule (CMR-hub records, cited with a prefix);
   `ADR-0023`/`ADR-0024` were deliberately skipped for sibling/hub citation
   reasons (see [`README.md`](README.md) numbering gap).
2. **Highest present.** `ADR-0026` is the highest file on `origin/master`.
3. **Citations.** `ADR-0027` has no file and no citation anywhere in the tracked
   tree (grep for `ADR-0027` outside `vendor/`, `.board/`, `.research/` returns
   nothing).

**Therefore this record lands as `ADR-0027`** — the next sequential number above
the measured floor of `ADR-0026`.

## Context

### The silo this decision closes

The measured current state (Phase 1) shows money and tokens flowing through
**disconnected surfaces** with no single authority over the ledger:

- **PF-1 (#666)** — `docs/erp-finops/current-state.md`: the money/token flow map
  (acquisition → CRM → invoicing → ledger → reporting) with every manual
  reconciliation point marked. It separates **front-end acquisition silos** from
  **back-end ledger silos** explicitly.
- **PF-2 (#667)** — `docs/erp-finops/token-baseline.md`: the measured DeepSeek
  token economics (`prompt_cache_hit_tokens` vs `prompt_cache_miss_tokens`) and
  the inventory of every CRM conversion hook.
- **PF-3 (#668)** — `docs/erp-finops/saas-metrics-current-state.md`: MRR/ARR
  tracking, per-surface cloud compute burn, and per-step manual invoicing
  bottlenecks, extending PF-1's silo map with the metric-flow version of each
  silo.

**Status note:** the PF-1 and PF-3 docs are referenced here **by path** and are
not yet present on this lane's base — the sibling lanes #666 and #668 had not
landed them at claim time. This ADR records the *architectural decision*
independently of those measurements; it does not quote a number that the
Phase-1 lanes have not yet committed.

The consequence of the silo is two-fold and is why it is an architecture
decision rather than a bookkeeping fix:

1. **Acquisition and ledger diverge.** A customer conversion lands in the CRM
   front-end, but revenue is recognised in manual invoices and spreadsheets, so
   the source of truth for "who owes what" is not machine-checkable.
2. **Inference spend is unbounded.** Token cost accrues per API call with no
   cache-efficiency discipline, so the same financial data can be re-sent to the
   model and re-billed with no reuse.

### What the epic mandates

EPIC #665 fixes the loop with one mechanical chain, stated in the epic itself:
**conversion event → ERPNext ledger → cache-optimal inference → metered, audited
cost**. The ERPNext module epic **#645** (ordered by #612) is the system-of-record
foundation this epic builds on; the production go-live **#607** is where the
measured outcome surfaces. This epic — and therefore this ADR — owns the
*financial and token economics* of that stack, not the ERP feature surface
(#645 owns that) and not the deploy (#607 owns that).

## Decision

### D1 — ERPNext is the financial system of record

**ERPNext is the single system of record for the control plane's financial
state.** Every revenue and token-cost figure the platform reasons over is
recorded in — and read back from — the ERPNext ledger, not a spreadsheet, not a
portal-side cache, and not a model's memory.

The module surface is owned by **#645** (`integrations/erp/`), whose definition
of done already fixes the pattern: ERPNext upstream is GPL-3.0 and a **pattern
source only** — no code is copied, every harvested shape records provenance
(GR-10), and the document model, transactional flows, REST surface and auth
scoping live behind a feature flag that defaults **OFF** (GR-5). This ADR does
not re-own that surface; it commits the *financial* semantics on top of it.

### D2 — Localized AI inference at the data's edge

**Inference over financial data runs at the edge of that data — colocated with
the ledger it reasons over — rather than shipping financial state to a remote
general-purpose endpoint.**

This is a data-sovereignty and cost decision, not a latency preference:

- **Sovereignty.** Financial records are the most sensitive data the platform
  holds (GR-6's spirit: the fewer surfaces a secret or a ledger figure crosses,
  the fewer places it can leak). Localizing inference keeps financial state out
  of an unbounded remote hop.
- **Cost.** The DeepSeek call is metered per token; a remote round-trip that
  re-sends the same context re-bills it. Edge-local inference reads the ledger
  where it lives and only the *minimum* context crosses the model boundary.

"At the edge" means the inference path is co-located with `integrations/erp/`'s
ledger data and gated by the same tenant identity/RBAC scope that protects the
ERP module — never a second, wider-clearance read path.

### D3 — The chain: conversion event → ERPNext ledger → cache-optimal inference

**The financial path is a single mechanical chain, and no rung may be bypassed:**

```
conversion event  →  ERPNext ledger  →  cache-optimal inference  →  metered, audited cost
```

- **Conversion event** is the customer moment the CRM front-end raises (the
  conversion hooks PF-2 inventories); it is a trigger, never a store.
- **ERPNext ledger** is the only durable write (D1).
- **Cache-optimal inference** is the only place a model touches financial data
  (D2, D4).
- **Metered, audited cost** is the only way the spend is reported — on the
  existing telemetry ledger rails, not a second accounting.

A rung that reads a spreadsheet, or bills a model call outside the metered path,
is a violation of the chain, not a shortcut.

### D4 — DeepSeek prefix-template + cache discipline are architectural requirements

**Cache efficiency is a hard architectural requirement, not an optimization.**
The inference layer must maximize `prompt_cache_hit_tokens` and minimize
`prompt_cache_miss_tokens` (the split PF-2 measures), by construction:

- **Prefix-template discipline** (the #670 lane's surface): a stable, fixed
  system-prefix + template so repeated financial reasoning reuses the cached
  prefix instead of re-billing the whole context. The template is a *contract*,
  not a hint — a call that cannot name its template cannot claim a cache hit.
- **Cache discipline as a gate:** the FinOps metering treats an uncached miss as
  a cost the caller must justify, not the default. A high miss ratio is a
  finding, reported on the same ledger as D3's metered cost.

This makes the token budget a *designed* property of the chain rather than an
emergent one.

### D5 — Relationship to the ERP module epic and the deploy epic

Three epics touch ERPNext; this ADR owns exactly one of the three:

| Concern | Owner | This ADR's stance |
|---|---|---|
| **Feature surface** (`integrations/erp/`: document model, REST, auth, metering) | **#645** (ordered by #612) | Consumed, not re-owned — the module surface is #645's lane. |
| **Deploy / go-live** (production at ai.purebliss.app) | **#607** | Consumed, not re-owned — the measured outcome surfaces there. |
| **Financial-architecture decision** (system of record, inference locality, cache discipline) | **This ADR (#669)** | Owned here. |

The boundary is: **#645 decides *what the ERP module can do*; #607 decides *when
it is live*; this ADR decides *what the financial architecture must be*** on top
of both. A later lane cannot silently re-point the ledger at a non-ERPNext store
or move inference off the edge without a **new** ADR superseding this one.

### Alternatives considered, and why each was rejected

| Alternative | Rejected because |
|---|---|
| **Keep the spreadsheet/manual-invoice status quo** (record the silo, change nothing) | It *documents* the divergence between acquisition and ledger but does not close it. The silo is the thing the epic exists to remove; ratifying it as-is leaves the "who owes what" question unanswerable by machine. |
| **A generic Postgres-as-ledger** (in-house financial tables) | It re-implements double-entry accounting, journaling and reconciliation from scratch — a supply-chain and correctness risk that ERPNext already solved. #645's definition of done already names ERPNext as the pattern source, so a bespoke ledger would also fork that lane. |
| **Remote, general-purpose inference** (always send financial state to a central model endpoint) | It re-bills the same context on every call (violates D4) and widens the clearance surface for financial data (violates D2's sovereignty rationale). Localized inference is the whole point of the token-optimal mandate. |
| **Cache discipline as a later optimization** (ship inference, tune cache afterwards) | It leaves the unbounded-spend problem in place until "later", and cache behaviour is retrofitted rather than designed. D4 makes it a *requirement* precisely because an uncached miss is a cost that only compounds while it is unmeasured. |

## Consequences

- **Positive.** The acquisition→ledger divergence is closed: a conversion has
  exactly one durable destination (the ERPNext ledger), and revenue is
  machine-checkable instead of spreadsheet-reconciled. Inference spend becomes a
  *designed* property of the chain (prefix-template + cache discipline) rather
  than an emergent bill, which is the elite cost-reduction target the epic
  mandates. Financial data stops crossing an unbounded remote hop.
- **Negative.** Two real costs are accepted. First, **this ADR is inert until
  #645 lands** `integrations/erp/` — the ledger it names does not exist yet, so
  the decision is a *target-state* commitment with a prerequisite in another
  lane (the PF-4 lane's scope is the record, not the module). Second,
  **localized inference (D2) is more work** than calling a single remote
  endpoint: it requires the co-located read path, the tenant-scoped clearance,
  and the template contract before the first cached call can be claimed.
- **Neutral.** Nothing ships on. `integrations/erp/` is #645's lane and remains
  flag-gated OFF (GR-5) until it lands; production is #607's lane. This record
  changes no running code — it only fixes the *decision* the downstream lanes
  build against.
- **Follow-ups.** The #670 lane lands the prefix-template contract that D4 names
  as a requirement. The #671 lane (CRM→ERPNext webhooks) and #675 lane (e2e
  sandbox sim) are the mechanical proof of the D3 chain, and are `Blocked-by`
  #645/#607 per the epic's own dependency edges. If this decision is ever
  reversed — a different system of record, or remote-only inference — that is a
  **new** ADR superseding this one, never an edit of this record.

## Evidence

Every claim above is drawn from committed content on this lane's base,
`origin/master`:

- `docs/decision-records/` — highest ADR file `ADR-0026`, so the next sequential
  number is `ADR-0027` (numbering note above).
- EPIC #665 body — the mechanical chain "conversion event → ERPNext ledger →
  cache-optimal inference → metered, audited cost", and the statement that #645
  is the system-of-record foundation and #607 the go-live.
- #645 body — `integrations/erp/` feature surface, ERPNext-upstream GPL-3.0
  pattern-source-only rule, and flag-gated-OFF (GR-5) definition of done.
- #666 / #668 bodies — the PF-1 (`docs/erp-finops/current-state.md`) and PF-3
  (`docs/erp-finops/saas-metrics-current-state.md`) deliverables this record
  references by path; not yet landed on this lane's base at claim time.
- #667 body — the `prompt_cache_hit_tokens` vs `prompt_cache_miss_tokens`
  baseline that D4 makes an architectural requirement.
