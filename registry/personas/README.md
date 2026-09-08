# SME persona registry + library — persona cards, tenant-extensible (registry/personas)

> Owner lane: **registry** (issue #11, work item 07, phase 1). Parent: EPIC-00
> (issue #4). Doctrine: [`AGENTS.md`](../../AGENTS.md),
> [`docs/EXECUTION-PLAN.md`](../../docs/EXECUTION-PLAN.md),
> [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md),
> [`docs/GOLDEN-RULES.md`](../../docs/GOLDEN-RULES.md).

This tree ships the **SME persona registry + library**: packaged persona cards
(name, expertise, owned lanes, default model tier, guardrails) and the
persona-as-system-prompt model, tenant-extensible. It is the productized
version of the fleet's SME doctrine, and it is the layer *above* the
AgentProfile contract (issue #9): **a persona maps onto an AgentProfile** via
[`mapping.py`](mapping.py).

## The relationship to the frozen contracts

| Contract | Where | What it owns |
|---|---|---|
| AgentProfile + platform vocabulary | [`registry/profiles/`](../profiles/README.md) (issue #9) | the ten dispatchable profile fields; closed tool/capability/constraint/guardrail/tier/memory ids in [`catalog.yaml`](../profiles/catalog.yaml) |
| Prompt modules | [`registry/prompts/`](../prompts/README.md) (issue #13) | versioned, published `systemPromptRef` modules |
| Org RBAC | [`identity/rbac/`](../../identity/rbac/README.md) (issue #12) | the tenant/team/agent role model a persona's `owner` resolves into |

This lane **consumes** those field/vocabulary names — it never redefines them.
A persona card selects a `systemPromptRef` (issue #13 shape), and closed
`toolAllowlist` / `capabilitySet` / `constraintSet` / `defaultModelTier` /
`memoryScope` / `guardrailPolicyRef` ids (issue #9 catalog). `registry.py`
validates every card two layers deep: the JSON Schema
([`persona-card.schema.json`](persona-card.schema.json), embedded closed
enums) **and** fail-closed membership against the live
[`catalog.yaml`](../profiles/catalog.yaml), so the two can never silently
drift.

## What a persona card is

A **PersonaCard** is the SME-layer definition of a dispatchable subject-matter
expert. One file per persona under [`cards/`](cards/) (`cards/<id>.yaml`).
**Adding a file adds a persona — filename adds no code change**: the loader
discovers all cards by scanning the directory.

```yaml
id: security-sme            # ^[a-z][a-z0-9-]*$ — must equal the filename stem
version: 1.0.0              # SemVer of this card (published versions immutable)
tenant: platform            # 'platform' = platform-default; <tenant-id> = tenant card
name: Security SME          # display name
summary: "Adversarial security reviewer: fail-closed ..."   # one-liner
expertise: [security review, ...]     # areas of expertise (domain scoring)
ownedLanes: [security, guardrails]    # lanes/issues this persona owns
posture: reviewer           # executor | reviewer | auditor  (separation of duties)
defaultModelTier: HIGH      # LOW/MED/HIGH/MAX (flash/pro ladder, closed)
guardrailPolicyRef: reviewer-bundle   # atomic or bundle policy id (closed)
systemPromptRef: security-sme/primary@v1   # prompt-module ref (issue #13 shape)
toolAllowlist: [file_read, shell_exec, ...]   # closed platform tools
capabilitySet: [security-review, audit]       # closed platform capabilities
constraintSet: [verify-before-done, no-secrets]  # closed platform constraints
memoryScope: [session, repository]          # closed platform memory scopes
provenance:                                   # GR-10: read-only sources derived from
  - "kushin77/capital-underwriting scripts/agent/sme/security.txt"
guardrails: [ ... ]        # optional human-readable non-negotiables
```

Lifecycle state (draft / published / retired) is **not** a card field — it is
recorded in the append-only ledger [`versions/manifest.yaml`](versions/manifest.yaml)
by [`registry.py`](registry.py). This keeps a published card's content
immutable (sha256) exactly like the issue #9/#13 contracts: editing a
published card is an integrity violation until a **new version** is authored
and published.

## Tree layout

```text
registry/personas/
├── README.md                  # this file — the contract doc
├── persona-card.schema.json   # PersonaCard JSON Schema (draft-07), closed enums
├── registry.py                # registry API + CLI (discover/get/publish/retire/resolve)
├── mapping.py                 # persona -> AgentProfile mapping + reviewer doctrine
├── cards/                     # seed persona cards, one file per persona (15)
├── versions/
│   └── manifest.yaml          # append-only publish/retire ledger (sha256)
└── tests/                     # pytest suite (40 tests, offline)
```

## Registry API — tenant-scoped lifecycle

A persona belongs to a **tenant** or to the **platform default**
(`tenant: platform`). Tenants **extend** the platform library: a tenant
registry is composed with the platform cards directory, so it resolves
tenant-first with a documented platform fallback — a tenant card *shadows* the
platform card of the same id; a persona a tenant defines is never visible to
another tenant (no cross-tenant leakage).

`registry.py` (API + CLI):

| Operation | Semantics |
|---|---|
| `discover()` | scan the cards dir; every file validates; `{(tenant, id): card}` |
| `get(tenant, id)` | tenant card, else platform default, else `UnknownPersonaError` |
| `publish(tenant, id)` | freeze current `(tenant, persona, version)` + sha256 into the ledger as published; idempotent for the same snapshot; refuses editing a published version |
| `retire(tenant, id)` | append `status: retired`; retired personas no longer resolve |
| `resolve(tenant, id)` | returns the published persona; refuses unknown / unpublished / retired / integrity-violated personas |

**No unversioned ad-hoc persona dispatch**: a persona must be published before
it resolves, and `resolve` verifies the on-disk sha256 against the frozen
digest (mirrors the prompt-library governance rule, issue #13).

## Seed personas (15, all platform-default, published 1.0.0)

Assembled from the harvested SME library; provenance is recorded per card
(GR-10, [`docs/CANNIBALIZATION.md`](../../docs/CANNIBALIZATION.md)) and the
full provenance table lives in [`cards/`](cards/) as each card's `provenance`
field. Posture mix: 7 executors, 7 reviewers, 1 auditor.

| id | posture | tier | role (summary) |
|---|---|---|---|
| `orchestrator` | executor | MED | plan/decompose/dispatch across parallel lanes |
| `coder` | executor | LOW | author code, tests and PRs in its lane |
| `researcher` | executor | LOW | investigate / root-cause / gather evidence |
| `data-agent` | executor | MED | analytics, reporting, FinOps-aware |
| `debugging-sme` | executor | LOW | root-cause-not-symptom debugging specialist |
| `docs-author` | executor | LOW | docs writing; one-definition-never-a-second-copy |
| `sniper` | executor | LOW | surgical cleanup / git-hygiene strikes, terse |
| `reviewer` | reviewer | MED | general independent reviewer (verify-only) |
| `security-sme` | reviewer | HIGH | adversarial security/fail-closed review |
| `iac-sme` | reviewer | HIGH | IaC review (drift, least-privilege, flag-gated) |
| `qa-sme` | reviewer | LOW | gate honesty / no-false-green / negative controls |
| `architecture-sme` | reviewer | MAX | decisions-only ADR/contract-freeze review |
| `frontend-sme` | reviewer | MED | frontend-governance review (auth invariants) |
| `docs-sme` | reviewer | MED | docs-governance review (toctree reachability) |
| `auditor` | auditor | HIGH | adversarial evidence audit; never executes |

## Persona ↔ profile mapping ([`mapping.py`](mapping.py))

`materialize_profile(card)` maps a persona card onto an **AgentProfile**:
the persona *selects* its prompt (`systemPromptRef`), tools
(`toolAllowlist`), model tier (`defaultModelTier`) and guardrail policy
(`guardrailPolicyRef`) from the issue #9/#13 contracts; `owner` is derived as
`<tenant>/<id>`; the remaining profile fields (`constraintSet`,
`capabilitySet`, `memoryScope`, `version`) carry over from the card. Every
materialized profile is validated against the frozen AgentProfile schema
[`agent-profile.schema.json`](../profiles/agent-profile.schema.json) and the
live catalog — a persona that cannot produce a valid profile fails at
materialization time. `python3 registry/personas/mapping.py` runs an offline
demo that materializes all 15 personas and demonstrates a review assignment.

## SME reviewer doctrine (enforced)

Every PR/verdict gets an **assigned reviewer persona**, and separation of
duties is enforced mechanically (mapping.py):

- **Posture classes are rigid.** `executor` executes primary work; `reviewer`
  independently reviews an executor's PR/verdict; `auditor` adversarially
  audits claims/evidence. `guard_dispatch(card, role)` refuses to dispatch a
  persona outside its posture class.
- **An auditor persona can never be the executing persona of the task it
  audits.** Dispatching an auditor-posture persona as `executor` raises
  `AuditorCannotExecuteError`; the auditor is always distinct from the
  executor it audits. (Negative test:
  `tests/test_review_assignment.py::test_auditor_as_executor_of_its_own_audit_is_rejected`.)
- **A reviewer is never the executor of the work it reviews.** Because the
  posture classes are disjoint, review assignment is structurally free of
  self-review; `assign_reviewer` still refuses when no reviewer distinct from
  the executor exists, and `assert_reviewer_distinct` rejects an explicit
  self-review.
- **Assignment is domain-scored.** `assign_reviewer` / `assign_auditor` pick
  the best-fit persona by matching the PR/verdict subject against the persona
  id, owned lanes and expertise (the leaderboard *lenses-as-discriminating-
  questions* model) — the assigned persona is the one whose questions change
  the output.

## How a tenant extends

1. Create the tenant's own cards directory (a registry `cards_dir`).
2. Add persona card files: `tenant: <tenant-id>`, filename `<id>.yaml` — a
   file is a persona, no code change.
3. Compose the registry with the platform library (`platform_dir`) so the
   tenant inherits the platform personas and overrides any it needs by adding
   a same-id card.
4. `publish` the tenant's cards before dispatch; a retired or unpublished
   persona never resolves.

## Verification

```bash
# Registry: validate + lifecycle status (15 published personas)
python3 registry/personas/registry.py status
python3 registry/personas/registry.py validate cards/security-sme.yaml
python3 registry/personas/registry.py resolve security-sme
python3 registry/personas/registry.py publish coder          # freeze a version
python3 registry/personas/registry.py retire coder           # retire a persona

# Persona -> profile demo (materializes all 15 + review assignment)
python3 registry/personas/mapping.py

# Tests (40 offline; negatives: auditor-as-executor, self-review, integrity,
# unpublished/retired resolve, no-cross-tenant leak, add-a-file discovery)
python3 -m pytest registry/personas/tests -q -p no:cacheprovider

# Repo gate stays green
make verify
```

## Provenance (cannibalized and adapted, per docs/CANNIBALIZATION.md)

Persona cards were adapted — not copied — from the read-only fleet/hub
sources, generalised onto the platform closed vocabulary. Per-card provenance
is recorded in each card's `provenance` field. Principal sources:

| Source | Pattern adapted | Where it landed |
|---|---|---|
| `shared-frontend` `docs/SME-PROFILES.md` | fleet-standard persona cards (name/expertise/owned lanes/tier/guardrails) | the card shape; the SME seed set |
| `capital-underwriting` `scripts/agent/sme/*.txt` + `sme-dispatch.sh` | 10 drop-in personas (security/terraform/prisma-db/test-quality/debugging/frontend/docs/auditor/sniper/caveman) + domain dispatch | reviewer/executor seed content + posture doctrine |
| `leaderboard` `lib/lenses/*.md` (8 lenses) | specialist lenses as discriminating questions | domain-scored reviewer assignment |
| `CMR` `onboarding/agent-profiles/{role.schema.json, profiles/*.json}` + `docs/SME-PROFILES.md` | persona-card frontmatter + model floor/escalation + guardrails | `posture`, tier floors, verify-only reviewer tool sets |
| `shared-services` `.claude/agents/*` | packaged agent personas | executor/reviewer seed content |
| `shared-governance` `GLOBAL_STANDARDS/agent-identity.md` | every action leaves an auditable trace | the auditor persona + audit posture |
| `registry/profiles` (issue #9) | AgentProfile schema + catalog | `materialize_profile` + closed-vocabulary enforcement |

## Downstream consumers (who reads what)

| Later lane | Consumes |
|---|---|
| Registry service (issue #10) | persona cards as the record; `(tenant, id)` scoping |
| Onboarding seeds (issue #14) | copies platform personas as tenant starter persona libraries |
| Model gateways (phase 2) | `defaultModelTier` on the materialized AgentProfile |
| Guardrails (phase 4) | `guardrailPolicyRef` / `constraintSet` / `capabilitySet` |
| State-machine execution (phase 3) | persona → AgentProfile → `id`/`version` execution context |
| Autonomous ops (phase 8, #43) | reviewer assignment + auditor separation for merge governance |
