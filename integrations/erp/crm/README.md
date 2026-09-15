# `integrations/erp/crm/` — CRM, projects, quality and support

The ERP module's **CRM-family lane** (issue [#650](https://github.com/kushin77/agent-orchestrator/issues/650),
EPIC [#645](https://github.com/kushin77/agent-orchestrator/issues/645)): the
document surface and the flows for **leads → opportunities → customers**,
**projects / tasks / timesheets**, **quality inspections** and **support issues
with SLA ageing**. The feature-by-feature contract this lane builds against is
[`docs/ERP-MODULE-GAP-ANALYSIS.md`](../../../docs/ERP-MODULE-GAP-ANALYSIS.md)
(rows 7–10).

## The surface

Eight document kinds, one envelope, one declaration set:

| Family | Kind | The machine it runs |
|---|---|---|
| CRM | `lead` | `new → contacted → qualified → converted`, or `→ lost` |
| CRM | `opportunity` | `open → proposal → won`, or `→ lost` |
| CRM | `customer` | `active → dormant → active`, or `→ churned` |
| Projects | `project` | `planned → active → completed`, or `→ cancelled` |
| Projects | `task` | `open → in-progress → done`, or `→ cancelled` |
| Projects | `timesheet` | `draft → submitted → approved`, or `→ rejected → draft` |
| Quality | `inspection` | `pending → in-progress → passed`, or `→ failed → pending` |
| Support | `support-issue` | `open → in-progress → resolved → closed`, `resolved → open` reopens |

```mermaid
stateDiagram-v2
  direction LR
  [*] --> lead_new: create
  lead_new --> contacted --> qualified
  qualified --> converted: convert_lead
  converted --> [*]
  state opportunity {
    [*] --> open
    open --> proposal --> won
  }
  open --> won: (unreachable: proposal only)
```

## The flows

| Flow | What it does | What it refuses, by name |
|---|---|---|
| `convert_lead` | A qualified lead becomes an opportunity; both moves are audited | `not-qualified` (names the state it is in), `already-converted` (a lead converts once) |
| `win_opportunity` | A `proposal` opportunity becomes `won` and earns a customer | the machine's `illegal-transition`, naming the states it could reach |
| `log_timesheet` / `project_costs` | Entries accumulate against a task inside a project; cost is a **derived** rollup | `unknown-task`, `wrong-project`, `inactive-parent`, `duplicate-entry`, `currency-mismatch`, `invalid-value` |
| `record_inspection_outcome` / `reinspect` | Outcome transitions, with a failed inspection re-inspectable rather than terminal | `unknown-state` (an outcome the declaration does not have), `illegal-transition` (an outcome this state cannot reach) |
| `open_issue` / `age_issue` / `resolve_issue` | An issue ages into `ok` / `due` / `breached` from an **injected clock** | `unknown-policy` (an issue whose priority names no declared policy), `clock-regression`, `invalid-timestamp` |

Reads are pure and writes are audited: `project_costs` and `age_issue` change
nothing, so a board can be rendered without polluting the trail an auditor
reads; `record_sla_check` is the explicit write that records what was read.

## The three properties the lane is built on

1. **Every state change is a declared transition on a hash-chained rail.**
   `Document` is frozen and `workflow.advance` is the only way to move one, so a
   caller can ask for a transition, be refused, or receive the extended rail
   that records it — nothing else. `audit.Rail` is append-only by construction
   (`append` returns a *new* rail) and tamper-evident (`verify` re-derives every
   digest and back-link), following the `telemetry/ledger` discipline.
2. **The definitions are data, and they arrive through one seam.**
   `definitions.load` accepts a path *or an in-memory mapping*; every other
   module reaches its states, transitions, vocabularies and SLA policies only
   through the resulting `DefinitionSet`. The indexer-fed catalogue of EPIC #645
   replaces [`catalog/definitions.json`](catalog/definitions.json) as a change of
   *source*, not of code. An undeclared kind, vocabulary or policy is refused by
   name — never defaulted.
3. **Every refusal is provoked.** [`negative_control.py`](negative_control.py)
   provokes all 27 codes of the closed refusal vocabulary and fails when the
   provoked set and the vocabulary diverge, so a new refusal cannot ship without
   a control that demonstrates it refusing. `scripts/check-erp-crm.sh` then
   requires the driver to go **red** against a mutant with its state machine
   neutered — a driver that cannot fail proves nothing about the controls it
   reports.

## Lane boundary (why this package is self-contained)

This package imports **nothing** from `integrations/erp/core` or
`integrations/erp/catalog`. Those were concurrent sibling lanes when this one was
cut (one issue = one lane = one branch), so the one shape the CRM lane needed —
the declaration set — is **declared locally** under [`catalog/`](catalog) and the
seam is named: `definitions.load`. There is no second copy of the core document
model here, and no shared shape is duplicated by accident.

Two further hand-offs are named rather than silently assumed:

* **The REST surface** is ERP-06, **the portal mount** is ERP-07, and **the
  database/transaction spine** is ERP-02/03 — this lane ships the documents and
  the flows, not a live surface. It therefore declares the flag it belongs to
  (`erp-module`, declared off in [`infra/feature-flags/registry.yaml`](../../../infra/feature-flags/registry.yaml))
  and serves nothing itself.
* **The indexer's definitions** are ERP-01's contract; until they land, the
  local declaration is authoritative and its `dataSource` field says so.

## Harvest provenance (GR-10)

Upstream ERPNEXT is **GPL-3.0** and is a **pattern source only**. No upstream
code is copied, vendored or translated. The four harvested shapes and what was
built instead are recorded in [`PROVENANCE.md`](PROVENANCE.md) and enforced
mechanically in [`catalog/provenance.json`](catalog/provenance.json) —
`provenance.load` refuses a record whose harvest claims copied code
(`code-copied`) and refuses an empty record too.

## Running it

```bash
python3 -m pytest integrations/erp/crm/tests -q     # the suite
bash scripts/check-erp-crm.sh                       # the gate (suite + check + mutant)
python3 -m integrations.erp.crm.cli check           # declarations, golden path, controls
python3 -m integrations.erp.crm.cli demo            # the golden-path transcript, as JSON
python3 -m integrations.erp.crm.cli definitions     # the validated declaration set
python3 integrations/erp/crm/negative_control.py    # provoke every refusal
```

The CLI follows the repository's tri-state contract: `0` OK, `1` NOT-OK,
`2` CANNOT-ASSESS (a declaration or harvest record that will not load is never a
pass).

## Layout

| Path | Role |
|---|---|
| [`model.py`](model.py) | the envelope, the closed kind/action/refusal vocabularies, `Refused`, `Finding`, `Document` |
| [`schema.py`](schema.py) | the stdlib JSON-Schema subset validator and its keyword freeze |
| [`definitions.py`](definitions.py) | the declaration set, its graph invariants, and the indexer seam |
| [`documents.py`](documents.py) | envelope parsing and per-kind field validation |
| [`workflow.py`](workflow.py) | the state machines and the audited `advance` |
| [`audit.py`](audit.py) | the append-only, hash-chained rail |
| [`timesheet.py`](timesheet.py) | timesheet accumulation and the derived cost rollup |
| [`sla.py`](sla.py) | deterministic SLA ageing from an injected clock |
| [`provenance.py`](provenance.py) | the GR-10 harvest record and its enforcement |
| [`flows.py`](flows.py) | the workspace and the end-to-end flows, including `golden_path` |
| [`cli.py`](cli.py) | `check` / `demo` / `definitions`, tri-state |
| [`negative_control.py`](negative_control.py) | one provocation per refusal, coverage computed |
| [`schema/`](schema) | the frozen JSON Schemas (`document`, `definitions`, `provenance`) |
| [`catalog/`](catalog) | the local declaration set and the harvest record |
| [`tests/`](tests) | the suite, with a negative control per validator |
