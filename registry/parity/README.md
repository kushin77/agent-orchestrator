# `registry/parity` — registry <-> canonical CMR parity gate (issue #145)

The registry is a **consumer** of the CMR role taxonomy. This package fails the
build when the vocabulary the registry *declares* drifts from the canonical CMR
catalog, so a tenant can address any canonical role without the registry forking
a parallel taxonomy.

## Two modes

| Mode | Compares | When to run | Needs `vendor/CMR` |
|---|---|---|---|
| **default (offline)** | registry vocabulary ↔ the **frozen baseline** (`canonical/cmr-role-vocabulary.json`) | every clone / CI / `make verify` | no |
| **`--verify-source`** | frozen baseline ↔ the **live `vendor/CMR` source** (sha256 + vocabulary) | when you want to know if the freeze is stale | yes |

The default mode is deterministic and offline, so it is the mode that can be
wired into `make verify` even in a fresh worktree where the `vendor/CMR`
submodule is unpopulated. `--verify-source` re-checks the freeze against the
live submodule and refuses (rc 2) when it is absent.

## What it compares

| Axis | Registry declaration | Canonical source |
|---|---|---|
| roles | `definitions.roleId.enum` in `registry/profiles/agent-profile.schema.json` and `registry/personas/persona-card.schema.json` | frozen baseline `roles` (from `vendor/CMR/onboarding/agent-profiles/role.schema.json` `properties.role.enum`) |
| model tiers | `definitions.modelTier.enum` | baseline `tiers` |
| worker models | `definitions.workerModel.enum` | baseline `models` |
| lanes | `definitions.canonicalLane.enum` | baseline `lanes` |

It also checks **membership**: every committed card/seed that declares a `role`
or `canonicalLanes` value must use an id from the frozen canonical set.

## The frozen baseline

`canonical/cmr-role-vocabulary.json` is the committed freeze of the canonical
CMR vocabulary. It is frozen **because `vendor/CMR` is an unpopulated submodule
in a fresh git worktree** — the live source cannot be read there, so the gate
would otherwise return CANNOT-ASSESS on every clone and could never be enforced.

Its `_provenance` block records the vendor repo, the exact source **path**, the
**sha256** of that source file at extraction time, a `payload_sha256` digest over
the four axes, the extraction date, and the refresh/verify commands. The gate
verifies the payload digest offline (`payload_sha256` mismatch ⇒ rc 1) so the
frozen vocabulary cannot be edited silently.

**Refresh it from a checkout where the submodule is populated** (this
recomputes the axis sets, the source sha256 and the payload digest, and rewrites
the file):

```bash
python3 registry/parity/parity.py --refresh-baseline
# or, from anywhere with an explicit root:
python3 registry/parity/parity.py --refresh-baseline --cmr-root /path/to/vendor/CMR
```

Then commit the refreshed baseline and record the new sha256 in
`docs/REGISTRY-PROVENANCE.md`.

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
| 0 | OK | the two vocabularies under comparison are equal |
| 1 | NOT-OK | drift, the two registry schemas disagree, or the frozen baseline was edited (integrity failure) |
| 2 | CANNOT-ASSESS | a required source is absent/unreadable — the frozen baseline (offline) or the live `vendor/CMR` source (`--verify-source`). **Never 0** |

An unreadable source is not agreement. In a fresh git worktree the `vendor/CMR`
submodule is unpopulated, so `--verify-source` returns rc 2 rather than a false pass.

## Running it

```bash
bash scripts/check-registry-parity.sh                     # default: offline
bash scripts/check-registry-parity.sh --verify-source     # freeze vs vendor/CMR
bash scripts/check-registry-parity.sh --refresh-baseline  # re-freeze (populated vendor)
bash scripts/check-registry-parity.sh --json              # machine-readable
bash scripts/check-registry-parity.sh --self-test         # prove it can fail
python3 -m pytest registry/parity/tests -q                # behavioral tests
```

`--self-test` proves five refusal paths fire: a registry-only role, a
canonical-only role, a hidden frozen baseline, a hidden vendor source, and an
edited baseline payload — so the comparison cannot pass vacuously.

## Fixtures

`fixtures/canonical/` is a hermetic stand-in for `vendor/CMR` (role schema +
catalog dir) so the tests run without the submodule; `fixtures/drift-canonical/`
carries one extra canonical role to exercise the live-source drift direction.
The committed `canonical/cmr-role-vocabulary.json` is asserted equal to the
hermetic fixture, so the freeze and the tests cannot silently diverge.

## Wiring

The gate ships **not yet wired** into `make verify`: `Makefile` and
`scripts/verify.sh` are single-writer files owned by the orchestrator, which
indexes this gate after merge. The default (offline) mode is the one to wire — it
needs no network, no `vendor/`, and only stdlib + PyYAML.
