# AgentIdentity parity — one shared schema, drift-detected both ways (#346)

> Owner lane: **registry** (issue #346). Parent: #338. Contract doc:
> [`docs/AGENT-IDENTITY.md`](../../../docs/AGENT-IDENTITY.md). Doctrine:
> [`AGENTS.md`](../../../AGENTS.md).

This namespace holds the parity engine for the **one shared agent-identity
schema** that both `agent-orchestrator` and `shared-frontend` validate against.
It is what makes the schema a *contract* rather than a document: a vocabulary or
required-field change on either side of it fails
[`scripts/check-agent-identity-parity.sh`](../../../scripts/check-agent-identity-parity.sh)
until the change is reconciled in the shared schema — never forked.

## Tree

```text
registry/profiles/parity/
├── README.md   # this file
├── parity.py   # the engine: projection, closed-vocabulary checks, schema parity
└── cli.py      # offline CLI (tri-state exit; every input overridable)
```

No `__init__.py`: this is a namespace package, imported by path (the same
convention as [`registry/parity/`](../../parity/README.md)).

## What it checks

1. **Schema ↔ schema parity (both directions).** The shared
   [`agent-identity.schema.json`](../agent-identity.schema.json) must reproduce,
   exactly, the closed vocabularies this repo already declares in
   [`agent-profile.schema.json`](../agent-profile.schema.json) and
   [`catalog.yaml`](../catalog.yaml) — `modelTier`, `capabilityId`, `toolId`,
   `constraintId`, `memoryScopeValue`, `guardrailPolicyId`. Its `required` set
   must be this repo's required set plus the five identity fields, and its
   `properties` must declare every property this repo declares. Widen either
   side and the check reports `AI-SCHEMA-DRIFT`.
2. **The `status` union.** The single closed enum
   `registered|active|paused|retired` must be exactly the union of
   `agent-orchestrator`'s set and `shared-frontend`'s set, so neither repo can
   add a status unilaterally and neither may narrow below the other.
3. **Every seed, projected.** All `registry/profiles/seeds/*.yaml` are projected
   to the identity view and validated. The seeds are the input; there is no
   hand-maintained snapshot. A free-text capability, an out-of-vocabulary
   status or tier, a missing required field, a wrong type or an undeclared field
   is refused with its own stable code.

## Finding codes

| Code | Meaning |
|------|---------|
| `AI-SCHEMA-DRIFT` | a shared-schema vocabulary/field set disagrees with this repo's declared set (either direction) |
| `AI-MISSING-FIELD` | a required field is absent from the identity view |
| `AI-EXTRA-FIELD` | a field the shared schema does not declare |
| `AI-OUT-OF-VOCAB` | a value outside a closed vocabulary (free-text capabilities included) |
| `AI-WRONG-TYPE` | a value of the wrong JSON type |
| `AI-SHAPE` | any other constraint (pattern/format/length) violation |
| `AI-CANNOT-ASSESS` | an input the check needs is absent or unreadable |

A line reads `label: <CODE> field=<field>: <detail>`, so a refusal names the
seed (or `shared-schema`), the vocabulary or field it concerns, and the
offending value.

## Usage

```bash
python3 registry/profiles/parity/cli.py            # the check (0/1/2)
python3 registry/profiles/parity/cli.py --json     # machine-readable report
bash scripts/check-agent-identity-parity.sh        # the gate + its negative control
```

Every input is overridable — `--schema`, `--profile-schema`, `--catalog`,
`--seeds-dir` — which is how the gate runs its self-mutating control on a
scratch copy of the tree and proves it can fail.

Exit codes: `0` OK / `1` NOT-OK / `2` CANNOT-ASSESS. CANNOT-ASSESS (a missing
input, or `jsonschema`/`PyYAML` unavailable) is never reported as a pass.
