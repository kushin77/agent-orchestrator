# AgentProfile — declarative schema, validator, versioning (registry/profiles)

> Owner lane: **registry** (issue #9, work item 05, phase 1). Parent: EPIC-00
> (issue #4). Doctrine: [`AGENTS.md`](../../AGENTS.md),
> [`docs/EXECUTION-PLAN.md`](../../docs/EXECUTION-PLAN.md),
> [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md),
> [`docs/GOLDEN-RULES.md`](../../docs/GOLDEN-RULES.md).

This tree defines the **AgentProfile**: the declarative unit *every other
pillar operates on* (registry service, persona mapping, model gateways,
guardrails, state-machine execution, telemetry). It is the **contract** for the
fleet: field names here are frozen (contract-freeze doctrine in
`docs/EXECUTION-PLAN.md`) — later lanes consume these names; they do not change
them.

## What an AgentProfile is

An AgentProfile declares, for one dispatchable agent role, the ten contract
fields below: who it is (`id`, `version`, `owner`), what prompt it runs under
(`systemPromptRef`), what it may touch (`toolAllowlist`), what boundaries bind
it (`constraintSet`), what it may do (`capabilitySet`), how it is routed
(`defaultModelTier`), what memory it may use (`memoryScope`), and which
guardrail policy is bound to it (`guardrailPolicyRef`).

## Tree layout

```text
registry/profiles/
├── README.md                   # this file — the contract doc
├── agent-profile.schema.json   # AgentProfile JSON Schema (draft-07), closed vocabularies
├── catalog.yaml                # canonical closed platform vocabulary (tools/capabilities/…)
├── validate.py                 # offline validator (CLI + importable); real exit codes
├── seeds/                      # published seed profiles (immutable, one file per version)
│   ├── PROVENANCE.md           # provenance table for every seed
│   └── <id>.<version>.yaml     # e.g. orchestrator.1.0.0.yaml, coder.1.0.0.yaml
├── versions/
│   └── manifest.yaml           # published-version ledger (sha256-locked, immutable)
└── tests/
    ├── test_agent_profiles.py  # pytest suite (offline)
    └── fixtures/               # deliberately broken profiles (self-test must reject each)
```

## The contract (ten fields, all required)

Validated by [`agent-profile.schema.json`](agent-profile.schema.json)
(`additionalProperties: false` — no undeclared field, no silent semantic
addition). Canonical meaning:

| Field | Type | Semantics |
|---|---|---|
| `id` | string | Stable id, pattern `^[a-z][a-z0-9-]*$` (e.g. `orchestrator`, `data-agent`). Registry service and persona mapping address agents by this id. |
| `version` | string | SemVer `X.Y.Z` of *this profile*. Once published (in `versions/manifest.yaml`) an `(id, version)` is **immutable** — see [Versioning](#versioning--immutable-published-versions). |
| `owner` | string | Accountable owner of the profile lifecycle, `namespace/team` (e.g. `platform/quality`). Identity/RBAC (phase 6) consumes this as a principal/team ref. |
| `systemPromptRef` | string | Pinned ref to a versioned prompt module, `<module>/<name>@v<n>` (e.g. `coder/primary@v1`), mirroring the harvested gmail-agent `prompts/<task>/v1.ts` convention. Resolution is owned by the prompt-library lane (issue #13); this schema pins the reference shape. |
| `toolAllowlist` | string[] | Closed allowlist of tool ids. Unknown tool ⇒ rejected (fail closed). |
| `constraintSet` | string[] | Boundary constraints (deny/require) from the closed constraint catalog. |
| `capabilitySet` | string[] | Closed capability set. Unknown capability ⇒ rejected (fail closed). |
| `defaultModelTier` | enum | `LOW`/`MED`/`HIGH`/`MAX` on the flash/pro ladder (LOW/MED → flash, HIGH/MAX → pro). Model gateways (phase 2) route on this. |
| `memoryScope` | enum[] | Scopes the profile may read/write: `user`, `session`, `repository` (vscode-memory `MemoryType` semantics). At least one required. |
| `guardrailPolicyRef` | enum | Single ref to a guardrail policy id (atomic **or** bundle). Unknown policy ⇒ rejected (fail closed). |

Example (abridged from [`seeds/coder.1.0.0.yaml`](seeds/coder.1.0.0.yaml)):

```yaml
id: coder
version: 1.0.0
owner: platform/execution
systemPromptRef: coder/primary@v1
toolAllowlist: [gh_issue, gh_pr, file_write, shell_exec]
constraintSet: [issue-first, stay-in-lane, no-direct-push, no-secrets]
capabilitySet: [code-author, test-author, test-run]
defaultModelTier: LOW
memoryScope: [session, repository]
guardrailPolicyRef: worker-bundle
```

## Closed vocabulary + fail-closed doctrine

The platform vocabulary is **closed** by default. [`catalog.yaml`](catalog.yaml)
is the canonical registry (each id carries a summary and its harvest source).
[`agent-profile.schema.json`](agent-profile.schema.json) embeds the same ids as
`definitions` enums. `validate.py` runs a **schema↔catalog parity gate** so the
two can never drift.

A profile that references an unknown tool, capability, constraint, guardrail
policy, tier or memory scope is rejected by **both** layers:

1. the JSON Schema closed enum rejects it at the schema layer, and
2. `validate.py` membership checks reject it at the code layer.

No unknown reference is ever silently allowed (no-false-green; AO-GR-4).
Extending the platform vocabulary is owned by the registry lane: add the id to
`catalog.yaml` **and** to the matching `definitions` enum in the schema; the
parity gate fails if you update only one.

## Versioning — immutable published versions

Seed profiles are published as immutable snapshots, one file per version under
[`seeds/`](seeds/), recorded in the ledger
[`versions/manifest.yaml`](versions/manifest.yaml) with `(profile, version,
file, sha256)`.

The validator **refuses**:

- **mutation of a published version** — if a seed file's on-disk sha256 differs
  from the recorded sha256 for its `(id, version)`, the gate fails. Publishing a
  change requires a **new** version file, never an edit of a published one;
- duplicate `(profile, version)` ledger entries;
- a seed file that is not in the ledger (nothing ships unpublished);
- a ledger entry whose file is missing or not named `<id>.<version>.yaml`;
- a seed whose declared `id`/`version` does not match its filename.

To publish a new version:

```bash
# 1. author the new snapshot
#    registry/profiles/seeds/<id>.<X>.<Y>.<Z>.yaml
# 2. append one entry to versions/manifest.yaml with:
sha256sum registry/profiles/seeds/<id>.<X>.<Y>.<Z>.yaml
# 3. gate must stay green
python3 registry/profiles/validate.py
```

## Seed profiles

Five published seeds ship with the contract (derived from harvested personas;
provenance in [`seeds/PROVENANCE.md`](seeds/PROVENANCE.md)):

| Seed | Role | Default tier | Highlights |
|---|---|---|---|
| `orchestrator` | orchestrator/planner | MED | orchestrate + task-claim; board/issue/worktree tools |
| `coder` | coding worker | LOW | code-author/test-author/test-run; full write+PR toolchain |
| `reviewer` | reviewer/auditor (SME-lens) | MED | code-review/test-run/audit; reviewer-bundle guardrails |
| `researcher` | investigation/RCA | LOW | research/data-analysis; web_fetch + sql_query |
| `data-agent` | analytics/data | MED | data-analysis; sql_query/file_write; FinOps-aware |

## Validation

```bash
python3 registry/profiles/validate.py                     # seeds + catalog parity + ledger → exit 0
python3 registry/profiles/validate.py --self-test         # + every tests/fixtures/*.yaml must FAIL → exit 0
python3 registry/profiles/validate.py seeds/coder.1.0.0.yaml   # validate one profile
python3 -m pytest registry/profiles/tests -q              # pytest suite (offline)
```

Exit codes: `0` all good; `1` invalid (profile, fixture accepted, parity drift,
or ledger inconsistency); `2` usage error. `make verify` stays the repo gate of
record; this validator is its own honest, offline check with real exit codes.

## Downstream consumers (who reads what)

| Later lane | Consumes |
|---|---|
| Registry service (issue #10) | `id`, `version`, seed YAML as the record; catalog.yaml as the vocabulary store |
| Persona mapping (issue #11) | `id` ↔ persona role mapping; `systemPromptRef` |
| Model gateways (phase 2) | `defaultModelTier` for routing; `owner` for key scoping |
| Guardrails (phase 4) | `toolAllowlist`, `constraintSet`, `capabilitySet`, `guardrailPolicyRef` (expand bundles via catalog.yaml) |
| State-machine execution (phase 3) | `id`, `version` for execution context; `capabilitySet` bounds |
| Onboarding seeds (issue #14) | copies these seed profiles as tenant starter agents |
| Memory (vscode-memory pattern) | `memoryScope` (user/session/repository) |
