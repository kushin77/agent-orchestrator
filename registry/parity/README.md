# `registry/parity` — registry <-> canonical CMR parity gate (issue #145)

The registry is a **consumer** of the CMR role taxonomy. This package fails the
build when the vocabulary the registry *declares* drifts from the canonical CMR
catalog, so a tenant can address any canonical role without the registry forking
a parallel taxonomy.

## What it compares

| Axis | Registry declaration | Canonical source |
|---|---|---|
| roles | `definitions.roleId.enum` in `registry/profiles/agent-profile.schema.json` and `registry/personas/persona-card.schema.json` | `vendor/CMR/onboarding/agent-profiles/role.schema.json` `properties.role.enum` |
| model tiers | `definitions.modelTier.enum` | `properties.model.properties.tier.enum` |
| worker models | `definitions.workerModel.enum` | `properties.model.properties.model.enum` |
| lanes | `definitions.canonicalLane.enum` | `properties.ownedLanes.items.enum` |

It also checks **membership**: every committed card/seed that declares a `role`
or `canonicalLanes` value must use an id from the canonical set.

## Direction (the contract)

The doctrine is **reuse, never fork**; the two sets must be **equal**, not one a
subset of the other:

* a **registry-only** id is drift — the registry invented a role/tier/model/lane
  CMR does not publish;
* a **canonical-only** id is drift — the registry failed to backfill a canonical
  role;
* the profile schema and the persona schema **disagreeing** on an axis is
  NOT-OK (internal drift).

## Tri-state exit codes

| rc | state | meaning |
|---|---|---|
| 0 | OK | registry vocabulary equals the canonical CMR vocabulary |
| 1 | NOT-OK | drift in either direction, or the two registry schemas disagree |
| 2 | CANNOT-ASSESS | the canonical source is absent/unreadable — **never 0** |

An unreadable source is not agreement. In a fresh git worktree the `vendor/CMR`
submodule is unpopulated, so the honest outcome is rc 2, not a false pass.

## Running it

```bash
bash scripts/check-registry-parity.sh                     # gate (rc 0/1/2)
bash scripts/check-registry-parity.sh --json              # machine-readable
bash scripts/check-registry-parity.sh --self-test         # prove it can fail
bash scripts/check-registry-parity.sh --cmr-root DIR      # alternate canonical root
python3 -m pytest registry/parity/tests -q                # behavioral tests
```

`--self-test` mutates aligned in-memory vocabularies and proves a registry-only
role, a canonical-only role and a hidden canonical root are all caught, so the
comparison cannot pass vacuously.

## Fixtures

`fixtures/canonical/` is a hermetic stand-in for `vendor/CMR` (role schema +
catalog dir) so the tests run without the submodule; `fixtures/drift-canonical/`
carries one extra canonical role to exercise the canonical-to-registry direction.

## Wiring

The gate ships **not yet wired** into `make verify`: `Makefile` and
`scripts/verify.sh` are single-writer files owned by the orchestrator, which
indexes this gate after merge. It needs no network and only stdlib + PyYAML.
