# control-plane/instructions — Model-agnostic instruction layer + per-tool mirrors

> Owner lane: `control-plane/instructions/**` (issue `kushin77/agent-orchestrator#42`,
> "38 Model-agnostic instruction layer + per-tool mirrors", work item 38,
> phase 7).  Parent: EPIC-00 (issue #4).  Doctrine: [`../../AGENTS.md`](../../AGENTS.md),
> [`../../docs/EXECUTION-PLAN.md`](../../docs/EXECUTION-PLAN.md),
> [`../../docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md),
> [`../../docs/GOLDEN-RULES.md`](../../docs/GOLDEN-RULES.md).
> This subtree adds files ONLY under `control-plane/instructions/`; the
> foundation-owned root `AGENTS.md` / `CLAUDE.md` / `.cursorrules` are the
> pattern this productizes and are never edited here.

## What this is (30 seconds)

Agents are governed by instructions, but every harness reads a different file —
`AGENTS.md`, `CLAUDE.md`, `.cursorrules`, `copilot-instructions.md`.  If those
files are hand-maintained copies of each other they drift, and a governed repo
behaves differently under Claude, DeepSeek, Copilot or Cursor.

This lane productizes the fix the fleet already uses informally (this repo's
own canonical `AGENTS.md` + mirror pattern, CMR EPIC-18 / ADR-0007,
`customize-instructions.sh`, `standards-sync.sh`, shared-governance
`GLOBAL_STANDARDS`): **ONE canonical instruction source**; **per-tool mirrors
generated** (never hand-forked, deterministic, byte-stable); **tenant
customization bounded by a frozen contract**; **versioned distribution with a
per-consumer drift check**; and a **conformance suite** proving the same rules
and precedence reach every harness.  Everything is offline by construction
(Python stdlib + PyYAML; no network, no third-party templating).

## Acceptance criteria → where

| Acceptance criterion | Where |
|---|---|
| One canonical instruction source; per-tool mirrors generated (never hand-forked), deterministic + byte-stable | `schema/canonical.schema.json`, `aoi/model.py`, `aoi/render.py`, `example/rendered/` + `tests/test_render_deterministic.py` |
| Contract freeze on customizable fields (tenant overrides bounded by schema; out-of-schema override REJECTED) | `schema/tenant-override.schema.json`, `aoi/override.py`, `tests/test_override_contract.py` |
| Versioned standards distribution + per-consumer drift check | `schema/distribution-manifest.schema.json`, `schema/consumer-state.schema.json`, `aoi/versioning.py`, `tests/test_drift_detector.py` |
| Model-agnostic conformance suite (same rules + precedence in every mirror) | `aoi/conformance.py`, `tests/test_conformance.py` |

## Tree layout

```text
control-plane/instructions/
├── README.md                  # this file — contract + quickstart + provenance
├── aoi/                       # importable package (stdlib + PyYAML only)
│   ├── model.py               # canonical instruction-source model + validation
│   ├── override.py            # frozen tenant-override contract (pure-Python gate)
│   ├── render.py              # per-tool mirror GENERATOR (AGENTS/CLAUDE/.cursorrules/Copilot)
│   ├── versioning.py          # SemVer + distribution manifest + drift detector
│   ├── conformance.py         # structural-equivalence check across mirrors
│   ├── cli.py                 # aoi render / validate-override / drift / conformance / pin
│   └── __init__.py
├── schema/                    # machine-readable contracts (JSON Schema)
│   ├── canonical.schema.json
│   ├── tenant-override.schema.json
│   ├── distribution-manifest.schema.json
│   └── consumer-state.schema.json
├── example/                   # worked example: a small canonical set rendered
│   ├── canonical.yaml         #   to all four mirror formats
│   ├── tenant-override.json
│   ├── consumer-state.json    #   pinned (compliant) consumer
│   └── rendered/              #   COMMITTED generator output (byte-stable proof)
└── tests/                     # pytest suite (offline; runs the negatives too)
    ├── conftest.py
    ├── test_render_deterministic.py
    ├── test_override_contract.py
    ├── test_drift_detector.py
    ├── test_conformance.py
    └── test_cli.py
```

## The canonical model (AC1)

A canonical instruction source (`ao.instructions.canonical/v1`) is a structured
YAML/JSON document: an `id`, a SemVer `version`, a `title`/`summary`, and
`layers` ordered **most-governing first**.  Each layer has a `label`, a
`managed` flag (governed layers are frozen to tenants) and ordered `rules`
(`id` + model-agnostic `text`).  Rule ids are unique across the whole set.

```text
schema: ao.instructions.canonical/v1
id: example-governed-repo
version: "1.0.0"
layers:
  - id: platform        # governed (managed: true)  — highest precedence
  - id: agent-pack      # governed (managed: true)
```

See [`example/canonical.yaml`](example/canonical.yaml) and
[`schema/canonical.schema.json`](schema/canonical.schema.json).

## Per-tool mirrors: generated, never hand-forked (AC1)

`aoi render` renders the canonical source (+ optional tenant override) into all
four mirror formats at once — [`aoi/render.py`](aoi/render.py),
`MIRROR_TARGETS`:

| Mirror | Consumed by |
|---|---|
| `AGENTS.md` | generic / DeepSeek harness (model-agnostic) |
| `CLAUDE.md` | Claude Code |
| `.cursorrules` | Cursor |
| `copilot-instructions.md` | GitHub Copilot |

Each mirror is a **self-contained full rendering** of the canonical semantics:
the same ordered rule set and the same precedence order appear in every file,
plus a machine-readable ledger comment (`ao-instructions`) that the conformance
suite reads back.  The renderer is a pure function of its inputs — no
timestamps, no randomness, stable ordering — so:

```bash
# regenerate and prove the committed mirrors are NOT hand-forked (byte-stable)
python3 -m aoi render --canonical example/canonical.yaml \
    --override example/tenant-override.json --out example/rendered --check
# -> render: byte-stable — 4 mirrors match regeneration
```

A human hand-edit of any mirror breaks byte-stability and is a hard failure —
mirrors are generated artifacts, exactly like the `MANIFEST` parity of the
issue-#41 consumer template and this repo's `make verify` drift doctrine.

## Frozen tenant-override contract (AC2)

Governed content (the `managed: true` layers) is **frozen**.  A tenant may
customize ONLY within the closed, schema-bounded override contract
(`ao.instructions.override/v1` — see
[`schema/tenant-override.schema.json`](schema/tenant-override.schema.json)):
branding (`repository`, `subtitle`) and **extra local-layer rules**
(`local.extraRules`).  The contract is closed at every object level
(`additionalProperties: false`), so an override that attempts any field outside
it is **REJECTED**:

```bash
python3 -m aoi validate-override --override example/tenant-override.json \
    --canonical example/canonical.yaml
# -> override: valid (ao.instructions.override/v1)
```

Semantic freezes enforced beyond pure schema (in [`aoi/override.py`](aoi/override.py)):

- an extra rule id may not **collide with a governed rule id** (governed
  behaviour cannot be redefined);
- the override must target the **exact canonical id and version**.

`tests/test_override_contract.py` runs the full negative matrix (unknown
top-level field, unknown nested field, redefined governed id, wrong canonical
id/version, bad schema, out-of-pattern ids …) and proves the pure-Python
validator agrees with the JSON Schema on every case where both can judge.

## Versioned distribution + per-consumer drift (AC3)

Rendering writes a **distribution manifest**
(`ao.instructions.distribution/v1`): the canonical id + SemVer version and the
sha256 of every generated mirror.  A consumer pins its installed state in a
**consumer state** (`ao.instructions.consumer/v1` — see
[`schema/consumer-state.schema.json`](schema/consumer-state.schema.json)); the
drift detector compares the two:

```bash
python3 -m aoi drift --manifest example/rendered/distribution-manifest.json \
    --consumer example/consumer-state.json
# -> drift: compliant — example/consumer-state.json matches ...
```

Any of these is a drift (non-zero exit; negative-tested): a consumer **behind**
(or ahead of) the distribution version, a wrong canonical id, a **missing**
mirror, or a mirror whose sha256 differs — a **locally hand-edited mirror**.
This productizes the `MANIFEST` sha-parity of the issue-#41 consumer template,
the `.governance-version` pin of shared-governance, and the `VERSION` +
`standards-sync.sh` distribution model, offline and schema-bounded.

## Model-agnostic conformance (AC4)

The conformance suite proves the model-agnostic claim structurally: render ONE
canonical source for every tool target and assert the **same ordered rule set
and the same precedence order are present in every mirror** — and that each
rule's statement text is actually in the file (a ledger cannot claim a rule the
mirror does not carry):

```bash
python3 -m aoi conformance --dir example/rendered \
    --canonical example/canonical.yaml --override example/tenant-override.json
# -> conformance: PASS — identical rules + precedence in every mirror
```

Because each harness reads its own mirror (`AGENTS.md` for generic/DeepSeek,
`CLAUDE.md` for Claude Code, `.cursorrules` for Cursor,
`copilot-instructions.md` for GitHub Copilot), identical semantics across
mirrors means **the same task yields the same behaviour under every harness**.
`tests/test_conformance.py` also proves the negatives: a missing mirror, a
reordered rule, a hand-edited rule statement, or a mirror without a ledger all
fail conformance.

## CLI reference

```text
python3 -m aoi render            --canonical <file> [--override <file>] --out <dir> [--check]
python3 -m aoi validate-override --override <file> [--canonical <file>]
python3 -m aoi drift             --manifest <file> --consumer <file>
python3 -m aoi conformance       --dir <dir> --canonical <file> [--override <file>]
python3 -m aoi pin               --manifest <file> --consumer <name> --out <file>
python3 -m aoi --version
```

Exit codes: 0 pass, 1 failure (invalid override / drift / non-conformance /
byte-stable mismatch), 2 usage.

## Worked example

[`example/`](example/) is a small canonical instruction set (platform +
agent-pack governed layers) rendered — together with the Acme tenant override —
into all four mirror formats under [`example/rendered/`](example/rendered/).
The committed output IS the byte-stable proof artifact: the render tests
regenerate it and assert byte-equality, and the conformance tests assert
semantic equivalence across the four mirrors.  `example/consumer-state.json`
is a pinned, compliant consumer (the drift tests flip it to old/drifted states
to prove detection).

## Verification

```bash
python3 -m pytest control-plane/instructions/tests -q -p no:cacheprovider   # offline suite
make -C <repo-root> verify                                                  # repo gate stays green
```

## Provenance (cannibalized + adapted; GR-10)

Sources verified on disk under `.research/` (read-only mirrors) before reuse;
nothing was copied verbatim — the mechanisms were productized, tightened and
made offline/schema-bounded.  Full index: `../../docs/CANNIBALIZATION.md`.

| Source (repo, path on disk) | Pattern adapted | Where it landed |
|---|---|---|
| `CMR board/epics/EPIC-18-model-agnostic.md` | canonical source + per-tool files, edit-canonical-first, no brand in the agnostic layer | `aoi/model.py`, `aoi/render.py`, conformance doctrine (AC4) |
| `CMR controller/customize-instructions.sh` | deterministic, idempotent per-repo instruction generator (same profile -> byte-identical output) | `aoi/render.py` (`render --check` byte-stable gate) |
| `CMR controller/standards-sync.sh` + shared-governance `GLOBAL_STANDARDS/VERSION` + `governance-distribution.md` | versioned standards distribution to consumers; `.governance-version` pin model | `aoi/versioning.py`, `schema/distribution-manifest.schema.json`, `schema/consumer-state.schema.json` (AC3) |
| `CMR docs/decision-records/ADR-0007-module-json-extensible.md` | schema-bounded customization surface (schema is the contract) | `schema/tenant-override.schema.json` + `aoi/override.py` (AC2; closed freeze for governed content) |
| `CMR onboarding/*` + issue-#41 `control-plane/sdk/template/consumer-repo/` (00/10/20 layering, `MANIFEST` sha parity, `managed` markers) | governed/local instruction layering + drift manifest on the consumer side | canonical layer model + drift detection (AC1/AC3) |
| this repo root `AGENTS.md` / `CLAUDE.md` / `.cursorrules` + `shared-frontend` `AGENTS.md`/`CLAUDE.md`/`.cursorrules` | canonical + precedence + mirror pattern (the pattern being productized) | `aoi/render.py` mirror targets + `example/rendered/` |

## Relationship to the issue-#41 consumer template

The issue-#41 consumer template
(`../sdk/template/consumer-repo/`) wires governed instruction *layers* into a
consumer repo and ships a consumer-side `make verify`/`make sync` parity gate.
This subtree productizes the layer above it: the **model-agnostic mirror
generator + frozen tenant-override contract + versioned distribution** that a
tenant/control plane uses to *produce* the governed instruction files each
harness consumes.  The two are complementary — the consumer template is where
the generated mirrors land; this layer is the mechanism that makes them
model-agnostic, tenant-bounded and drift-checked.
