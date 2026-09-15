# Harvest provenance — `integrations/erp/crm/` (GR-10)

**Upstream is a pattern source only. No upstream code is copied, vendored or
translated into this module.**

| | |
|---|---|
| Upstream project | [`frappe/erpnext`](https://github.com/frappe/erpnext) |
| Upstream licence | **GPL-3.0** |
| Material read | public documentation (v16), read 2026-09-14 |
| Policy | `patterns-only-no-upstream-code` ([`catalog/provenance.json`](catalog/provenance.json)) |
| Code copied | **none** — enforced, not asserted |

## What was harvested, and what was built instead

The four shapes below were harvested from the upstream *documentation* as
patterns. Every one of them is re-expressed as this module's own declaration
data and own code: no upstream field name, doctype definition, Python module,
test, fixture or template was reproduced, and no upstream repository was cloned
into this checkout.

| Shape | Harvested pattern | What this module declares instead |
|---|---|---|
| `crm-funnel` | A lead moves through a funnel before it becomes an opportunity, and a lead's funnel *stage* is separate from its record *state* | `catalog/definitions.json` declares its own `lead-stages` / `opportunity-stages` vocabularies and its own `lead` / `opportunity` / `customer` machines |
| `projects-timesheet-accumulation` | A timesheet line is booked against a task that names its project, and project cost is *derived* rather than stored | `timesheet.accumulate` computes the rollup from the documents present, in integer minor units with this module's own rounding rule |
| `inspection-outcome-transitions` | An inspection runs `pending → in-progress → passed/failed`, and a failed inspection is re-inspectable | `catalog/definitions.json` declares the transition set, including `failed → pending` |
| `support-sla-ageing` | An issue ages against a response window and a resolution window taken from a policy its own priority names | `sla.age` reads whole-minute windows and an integer warning percentage from the declaration set and takes the clock as a parameter |

## Why this is enforced rather than promised

"We did not copy upstream code" is exactly the claim that stays true until
someone pastes a file, so it is not left as prose:

* [`catalog/provenance.json`](catalog/provenance.json) is the machine-readable
  record — one entry per shape, each naming its upstream project, version,
  licence, URL and harvest date, plus a `codeCopied` flag.
* [`provenance.py`](provenance.py) refuses a record that claims
  `codeCopied: true` **by name** (`code-copied`), and refuses an *empty* record
  too: an unfilled form would let a lane harvest silently by recording nothing.
* `negative_control.py` provokes both refusals, and
  `tests/test_provenance.py` asserts that no shipped harvest claims copied code.

## Maintenance

Adding a harvested shape means adding a `harvests` entry to
`catalog/provenance.json` **first** — the loader refuses a record whose policy is
weaker than `patterns-only-no-upstream-code`, so a new shape cannot arrive
without its source and licence recorded.
