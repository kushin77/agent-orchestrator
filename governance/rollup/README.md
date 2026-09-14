# governance/rollup — the enterprise/GDC roll-up (issue #151)

Repos hold fleets, fleets belong to tenants, tenants belong to the
enterprise/GDC org. This module computes the **org-level view** of that
hierarchy from declared inputs, and nothing else.

The view is a **projection, never a second source of truth**: it writes no file,
keeps no cache, mutates no input, and every number in it is a pure function of
the declarations it was handed. The report carries the sha256 of each input so a
reader can re-derive the same view from the same bytes —
`docs/ENTERPRISE-ROLLUP.md` states the rule and its blast radius.

## Inputs (the contract)

| File | Definition | Meaning |
| --- | --- | --- |
| `schema.yaml` | `#/definitions/org` | one enterprise, its tenants, each tenant's repos |
| `schema.yaml` | `#/definitions/inventory` | one repo's fleet for one window |

Only these two document kinds are read; there is deliberately no third input,
because a third input would be a second place for the truth to live.

## CLI

```bash
python3 governance/rollup/cli.py project          # the org view (committed pilot)
python3 governance/rollup/cli.py project --org … --inventory-dir … --json
python3 governance/rollup/cli.py validate         # the declarations alone
```

Exit-code contract: **0 OK / 1 NOT-OK / 2 CANNOT-ASSESS**.

* `1` — a real violation: an SME over its ceiling, a scope over its ceiling, two
  tenants claiming one repo, a closure rate above 100%.
* `2` — the view cannot be trusted: a missing or malformed declaration, an
  unreadable schema, an empty fleet, no capacity to divide by. CANNOT-ASSESS
  **dominates** NOT-OK, because an input set that could not be read can
  understate the findings of the part that could.

## What is computed

| Metric | Roll-up rule |
| --- | --- |
| SME inventory | every SME, identified as `repo#id`, listed per repo |
| utilisation | engaged hours / capacity hours, over the SMEs that declare capacity |
| spend vs ceiling | scope total, the scope's ceiling, the excess over it, and the SMEs over their own ceilings |
| closure rate | closed / dispatched |
| drift | drift findings / drift checks |

Nothing is clamped: an over-ceiling spend keeps its real value, a utilisation
above 100% is reported as the ratio it is, and a closure rate above 100% is
reported and flagged as an inconsistency rather than capped.

## Layout

| Path | Role |
| --- | --- |
| `schema.py` | fail-closed JSON-Schema subset (refuses keywords it does not implement) |
| `inputs.py` | reads and validates the declarations; collects problems instead of raising on the first |
| `model.py` | the projection engine (facts, findings, tri-state) |
| `cli.py` | operator CLI and the exit-code contract |
| `pilot/` | the committed pilot hierarchy and its declared week |
| `fixtures/` | the gate's provocations: over-ceiling, missing inventory, unsupported schema |
| `tests/` | behavioral suite (hierarchy, ceilings, projection, tri-state, negative controls) |

## Verify

```bash
python3 -m pytest governance/rollup/tests -q   # the behavioural suite
bash scripts/check-rollup.sh                   # the gate (provokes every refusal)
```

The gate is standalone by design: `scripts/verify.sh` is a shared file owned
elsewhere, and wiring a new check into it is the orchestrator's step after this
lane merges.
