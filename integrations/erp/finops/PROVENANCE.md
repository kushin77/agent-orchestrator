# Harvest provenance — `integrations/erp/finops/`

**Policy:** `patterns-only-no-upstream-code`.
**Machine-readable record:** [`catalog/provenance.json`](catalog/provenance.json)
(enforced by [`provenance.py`](provenance.py) and validated against
[`schema/provenance.schema.json`](schema/provenance.schema.json)).

This lane harvests **shapes**, never code. The record below is the human-facing
account; the JSON is the enforced one. `provenance.load` refuses a record that
claims `codeCopied: true` and refuses an empty record too, so the constraint is
measured rather than promised — a lane cannot harvest silently by recording
nothing, and cannot record a copy without failing.

## Why a record at all

GR-10 requires every cannibalized asset to name its source, path and licence.
`docs/ERP-MODULE-GAP-ANALYSIS.md` pins the constraint for the whole ERP module:
ERPNext is GPL-3.0, so its **shapes** may be harvested and its **code** may not be
copied. Two of the four harvests below are from *this repository* rather than from
upstream, and they are recorded with exactly the same discipline: an in-repo
harvest that skips the record is the same undocumented borrowing as an upstream
one, and only the URL changes.

## The harvests

### 1. `document-lifecycle-as-the-metered-unit` — frappe/erpnext (GPL-3.0, pattern source only)

**Harvested:** that an ERP document's *meaningful, billable* events are its
**creation** and its **state transitions** (`draft → submitted → completed`, with
a cancel path) — not its edits, and not its reads.

**Built instead:** this lane's own closed operation vocabulary (`create`,
`transition`), its own event names (`erp.document.created` /
`erp.document.transitioned`), and its own rate card. No ERPNEXT doctype
definition, hook, workflow JSON or line of Python is reproduced; the lifecycle
itself is read out of *this repository's* core model
(`integrations/erp/core`, ERP-02), which is where the state machines live.

### 2. `rail-with-a-derived-hard-stop` — `integrations/paperclip/budget.py` (MIT, in-repo)

**Harvested:** the shape that a **metering record**, a **charge derived from a
declared rail**, and a **hard stop derived from a cap** are three separate facts
with three separate names — the separation, not the arithmetic.

**Built instead:** this lane derives its stop from the platform budget ladder over
a spend ledger, and derives its charge from a declared rate card. Nothing from
`paperclip/budget.py` is copied or imported; the file is credited because the
shape was read there first.

### 3. `warn-to-block-budget-ladder` — `telemetry/budgets` (MIT, in-repo)

**Harvested:** the shape of the enforcement **decision** object
(`allow / warn / would_warn / would_block / block`), the soft-vs-hard cap
distinction, and the observe-vs-enforce mode.

**Built instead:** nothing — this is *consumed*, not reimplemented. The lane
constructs the platform's own `TenantBudgetPolicy` objects and calls
`BudgetEnforcer`; `translation` between the platform's decision and this lane's
refusal vocabulary is the only thing written here, which is what made a named
`budget-exhausted` refusal expressible at all.

### 4. `hash-chained-append-only-audit-record` — `telemetry/ledger` (MIT, in-repo)

**Harvested:** the shape of an append-only record (tenant, actor, action,
resource, evidence, timestamp) and the tri-state verification discipline
(`OK` / `NOT-OK` / `CANNOT-ASSESS`) — specifically that a cost should not be
published over a chain that does not verify.

**Built instead:** nothing — also consumed. The lane writes through
`open_ledger` / `LedgerStore.append` and reads through `verify_ledger`, and owns
no copy of the ledger's storage, hashing or key handling. Acceptance criterion 3
of issue #654 is precisely that: the pillar is consumed through its public API,
and the lane's check hashes every tracked file under `telemetry/` before and after
a full metered run to prove that none of them was edited.

## What is *not* harvested

* No ERPNEXT field names, doctype definitions, workflow JSON, hooks or Python.
* No vendored upstream code, in any form, at any commit.
* No second implementation of a platform mechanism this repository already ships
  (the ledger chain, the usage record, the budget ladder, the schema validator is
  this lane's own only because the repository keeps a per-lane keyword freeze).
