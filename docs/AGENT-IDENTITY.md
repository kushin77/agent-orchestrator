# AgentIdentity — one shared agent-identity schema (#346)

> Owner lane: **registry** (issue #346). Parent: #338. Doctrine:
> [`../AGENTS.md`](../AGENTS.md). Namespace:
> [`../registry/profiles/parity/README.md`](../registry/profiles/parity/README.md).
> Gate: [`../scripts/check-agent-identity-parity.sh`](../scripts/check-agent-identity-parity.sh).

## The rule

**One schema, both repos validate; a field change must be reconciled, not
forked.** The agent record is declared once, in
[`agent-identity.schema.json`](../registry/profiles/agent-identity.schema.json),
versioned (`$id` …`agent-identity.schema:v1`, `x-schema-version: 1`). Neither
repo carries a private copy of the vocabulary: a change to a closed vocabulary
or to the required-field set on either side fails the other side's parity gate
until the shared schema is updated to match.

## The drift this replaced

| Axis | `shared-frontend` `TeamAgent` | `agent-orchestrator` `AgentProfile` | Reconciled in the shared schema |
|------|------------------------------|-------------------------------------|---------------------------------|
| identity fields | `id, name, provider, modelTier, status, capabilities` | no `name`, `status` or `provider`; `id` only | all six are **required**; the profile's own fields stay required alongside them |
| `capabilities` | free text | closed `capabilityId` | **closed** `capabilityId` enum — free text is refused |
| `modelTier` | two tiers (fast/pro) | closed `LOW/MED/HIGH/MAX` | the **closed four-rung ladder** (a superset of both) |
| `status` | `active\|paused\|retired` | `registered\|active\|paused\|retired` | the **closed union** `registered\|active\|paused\|retired` |

A shared-frontend record and an agent-orchestrator record are different
documents, but they are the *same contract*: both validate against this one
schema, so neither can drift on a vocabulary without the other's gate turning
red.

## Closed vocabularies

Every enum is closed. A value outside one of these is refused by the schema and
by the parity gate — there is no "unknown, allow it" path.

| Field | Vocabulary | Source of truth |
|-------|-----------|-----------------|
| `modelTier`, `defaultModelTier` | `LOW`, `MED`, `HIGH`, `MAX` | [`catalog.yaml`](../registry/profiles/catalog.yaml) `tiers` |
| `status` | `registered`, `active`, `paused`, `retired` | the declared union of both repos |
| `capabilities`, `capabilitySet` | 15 `capabilityId` ids | `catalog.yaml` `capabilities` |
| `toolAllowlist` | 17 `toolId` ids | `catalog.yaml` `tools` |
| `constraintSet` | 10 `constraintId` ids | `catalog.yaml` `constraints` |
| `guardrailPolicyRef` | 13 atomic + bundle ids | `catalog.yaml` `guardrailPolicies` |
| `memoryScope` | `user`, `session`, `repository` | `catalog.yaml` `memoryScopes` |
| `role`, `model`, `transport`, `effort`, `capabilityTier`, `fallbackChain`, `canonicalLanes` | canonical CMR vocabularies | [`agent-profile.schema.json`](../registry/profiles/agent-profile.schema.json) |

`provider` is the one **shape-constrained** field rather than an enum: it is a
lowercase slug (`^[a-z][a-z0-9-]*$`) so free prose cannot drift into it, but its
*value set* is owned by the model-gateway provider registry and is deliberately
not duplicated here (a second list would be a second source of truth).

## The projection (why the seeds need no hand-maintained snapshot)

An agent-orchestrator seed is an `AgentProfile`; the shared schema describes an
`AgentIdentity`. The gate reconciles them with a **projection**, computed at
check time from the seed itself — nothing is stored, and a field the seed
already declares is **never** overwritten by a default:

| Identity field | Source in a seed |
|----------------|------------------|
| `name` | the seed's `name`, else its `id` |
| `provider` | the seed's `provider`, else `transport` → `deepseek`, `api` → `anthropic`, `session` → `anthropic`, else `unspecified` |
| `modelTier` | the seed's `modelTier`, else its `defaultModelTier` |
| `status` | the seed's `status`, else `registered` (declared but not yet activated) |
| `capabilities` | the seed's `capabilities`, else its `capabilitySet` |

Everything else passes through unchanged. Because a *declared* value is never
defaulted, a declared-but-invalid value (a free-text capability, an
out-of-vocabulary `status`) is always visible to the check.

## The gate

[`scripts/check-agent-identity-parity.sh`](../scripts/check-agent-identity-parity.sh)
runs the engine over the committed tree and then mutates a **scratch copy**,
requiring the validator to refuse each mutant:

| Control | Mutation | Required answer |
|---------|----------|-----------------|
| A | a required field stripped from a seed | NOT-OK, naming the field |
| B | a free-text capability added to a seed | NOT-OK, naming the field |
| C | a closed enum in the shared schema widened | NOT-OK, schema drift |
| D | the shared schema hidden | CANNOT-ASSESS — never OK |
| E | the pristine copy, after every mutation | still OK |
| F | the committed files, before and after | sha256 unchanged |

Each mutation must really change the file (sha256 compared) and the committed
files must be byte-identical afterwards, so the control can neither pass
vacuously nor pass by damaging the tree. Exit codes: `0` OK / `1` NOT-OK /
`2` CANNOT-ASSESS, and CANNOT-ASSESS is never reported as a pass.

## Where the other half lives

The `shared-frontend` half of this contract — its `TeamAgent` type, and the
adoption of `agent-identity.schema.json` there — is a **direction**, not an edit
made from this repo: it is that repo's lane to land, consuming the same schema
id. This repo does not edit another repo's files; the reconciliation signal is
the parity gate on both sides.
