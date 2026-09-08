# identity/onboarding — tenant onboarding + provisioning controller

The provisioning **controller core** for the AI-agent-orchestration platform
(issue #14, work item 10, phase 1; parent EPIC-00 issue #4). Owner lane:
**identity** — this subtree is `identity/onboarding/**` only. Doctrine:
[`AGENTS.md`](../../AGENTS.md), [`docs/EXECUTION-PLAN.md`](../../docs/EXECUTION-PLAN.md),
[`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md),
[`docs/GOLDEN-RULES.md`](../../docs/GOLDEN-RULES.md).

## What this is

An idempotent, all-or-nothing tenant provisioning pipeline, consumed as an
**operator path** (CLI), never as an anonymous HTTP endpoint:

```
tenant row  ->  IdP tenant mapping  ->  RBAC Org  ->  preset roles
            ->  first owner/admin   ->  seed packs (profile+persona+prompt)
            ->  base defaults       ->  active
```

Every step is *create-if-missing*, so a run that fails partway (or a re-run
over an already-provisioned tenant) is repaired by running it again — it
converges to an identical end state with no duplicates. If any step genuinely
fails, both stores roll back to their pre-run state: **no partial tenant** is
ever left behind.

Later identity phases (#35–#38) surface this to an authenticated
control-plane/portal path and swap the in-memory/file store for a real
database; this lane provides the controller core, the job model, the operator
CLI and the ready-check that they wire up.

## Provenance (cannibalization)

Adapted to Python from (read-only fleet/hub sources, per the cannibalization
index in [`docs/CANNIBALIZATION.md`](../../docs/CANNIBALIZATION.md)):

- `saas-rbac` `src/tenants/provisioning.ts` + `provision-cli.ts` — the
  create-if-missing idempotent tenant bootstrap, slug validation, owner-role
  fail-before-write, `--dry-run` operator CLI (READY-TO-REUSE).
- `CMR` `controller/onboard.sh`, `standards-sync.sh`,
  `customize-instructions.sh` — the declarative, idempotent onboarding flow and
  per-tenant instruction rendering from a closed profile schema
  (PATTERN-ONLY).
- `capital-underwriting` `prisma/schema.prisma`
  (`TenantProvisioningJob`) — the job record: status
  pending/running/completed/failed, attempt/maxAttempts, stepName/stepStatus,
  error, JSON audit log, timestamps.
- `shared-services` `automation/tenant-provisioning/` +
  `services/contract-manager/` — tenant registry/status vocab and contract
  layering (PATTERN-ONLY).
- `shared-temporal` `patterns/multi_tenancy.go`
  (`ProvisionTenantWorkflow`) — sequenced steps with compensation/rollback on
  failure (PATTERN-ONLY).

## Consumed contracts (field names frozen upstream)

This lane consumes, and does **not** redefine:

| Upstream | Consumed |
|---|---|
| `identity/rbac` (issue #12) | `Org`/`Role`/`Binding`, preset packs by `tenant_type`, `role_admin_permission`, `seed_org`, `grant_role`, role keys (`owner`, `admin`, …) |
| `registry/profiles` (issue #9) | seed profiles `registry/profiles/seeds/<id>.<version>.yaml`; `defaultModelTier`, `memoryScope` enums for the customization allowlist |
| `registry/prompts` (issue #13) | prompt modules `registry/prompts/modules/<taskType>.v<n>.yaml`; `taskType@version` refs for seeding + customization |
| `registry/personas` (issue #11) | persona cards `registry/personas/cards/<id>.yaml` — installed as starter persona seeds (on master; the controller also records them *deferred-by-contract* on a checkout that lacks the registry dir) |

## Tree layout

```text
identity/onboarding/
├── README.md                # this contract doc
├── model.py                 # Tenant, IdpTenantMapping, SeedItem, job/customization
│                            #   data types, closed vocabularies, defaults
├── store.py                 # InMemoryStore (persistence seam) + FileStore (JSON)
├── registry_assets.py       # resolves platform registry seed assets (read-only)
├── provisioning.py          # the idempotent all-or-nothing provision() pipeline
├── customization.py         # per-tenant instruction customization (allowlist)
├── jobs.py                  # provisioning-job runner: status, retry, audit
├── ready.py                 # tenant ready-check completeness report
├── cli.py                   # operator CLI (python3 -m identity.onboarding.cli)
├── seeds/
│   └── tenant-starter.yaml  # default starter seed pack for every tenant
└── tests/                   # pytest suite (offline): idempotency, atomicity,
                             #   customization allowlist, jobs/retry/audit,
                             #   ready-check
```

## The model

Pure data types in [`model.py`](model.py); persisted by the store:

- **`Tenant`** — the onboarding/identity row (`id` is the slug).
  `Org.id == Tenant.id` in the RBAC store (an Org *is* the tenant).
- **`IdpTenantMapping`** — maps the tenant to its identity-provider tenant
  (one IdP tenant maps to exactly one platform tenant).
- **`SeedItem`** — one starter seed (`profile`/`persona`/`prompt`),
  `installed` or `deferred` (persona deferral, below).
- **`TenantCustomization`** — the validated per-tenant instruction overlay.
- **`ProvisioningJob`** — status (`pending|running|completed|failed`),
  `attempt`/`max_attempts`, current `step_name`/`step_status`, `error` and a
  step-level `audit_log` (mirrors `TenantProvisioningJob`).

## Stores

[`store.py`](store.py) mirrors the `identity/rbac` store philosophy:

- **`InMemoryStore`** — offline default used by tests/embedded use. Exposes
  `resource_snapshot()` / `restore_resources()` so provisioning is
  all-or-nothing across the tenant-resource collections while provisioning-job
  records (attempt/audit history) stay durable across a rollback.
- **`FileStore`** — operator persistence: serializes every collection
  (deterministic key order, `_next_id` persisted so job ids stay monotonic
  across invocations) to a JSON file. Later identity phases swap in a database
  adapter behind the same accessors.

## Provisioning pipeline

[`provisioning.py`](provisioning.py). `provision(store, rbac_store, spec, …)`
implements the steps above against a `ProvisionSpec`:

1. **validate** — slug pattern, name, resolvable tenant-type pack, pack grants
   the role-admin permission, owner email shape, owner role in pack,
   IdP-tenant and domain uniqueness. Every refusal happens **before any
   write** (fail fast, no rollback needed).
2. **tenant row** — create if missing.
3. **IdP mapping** — create if missing (idempotent by tenant).
4. **RBAC org** — `add_org` if missing (org id == tenant slug).
5. **preset roles** — seed the tenant-type pack's roles via
   `identity/rbac`'s `seed_org`; a re-run over already-seeded roles converges;
   an org seeded from a *different* pack is refused.
6. **owner/admin** — `grant_role(owner_email → owner)` org-wide (idempotent).
   An ownerless tenant is legal but inert (saas-rbac doctrine).
7. **seed packs** — install the starter seeds from [`seeds/`](seeds/),
   resolved against the on-disk registries by
   [`registry_assets.py`](registry_assets.py). Profiles/prompts/personas that
   cannot be resolved abort the run (→ rollback). Persona seeds are recorded
   *deferred-by-contract* only on a checkout where the persona registry
   directory is absent (issue #11 is on master, so they normally install).
8. **defaults + activate** — base defaults present, tenant → `active`.

All-or-nothing is enforced with resource snapshots of both stores: on any
exception the onboarding tenant resources **and** the RBAC store are restored,
then the error re-raises. `dry_run=True` runs everything and rolls back at the
end (a preview), mirroring saas-rbac's `--dry-run`.

## Per-tenant instruction customization

[`customization.py`](customization.py). A tenant may override instruction
layering only inside the **allowed-fields allowlist** (fail closed):

| Field | Allowed values | Contract |
|---|---|---|
| `defaultModelTier` | `LOW`/`MED`/`HIGH`/`MAX` | AgentProfile tier enum (`registry/profiles`) |
| `memoryScope` | non-empty subset of `user`/`session`/`repository` | AgentProfile memory enum |
| `instructionLayers` | ordered `{layer, ref\|inline, priority}` | see below |

`instructionLayers` customize how tenant instructions layer on top of the
platform seed-pack underlay. A `system` layer may only carry a `ref` that
**resolves in the prompt-module registry** (`registry/prompts`, fail closed);
a `tenant` layer may carry a `ref` or `inline` text. Raw inline text is never
allowed at the `system` layer (system-prompt content is owned by the
registry). Rejected inputs — disallowed fields, unknown enums, unresolved refs,
system-layer inline text, duplicate priorities — raise
`OverlayValidationError` and write nothing. `render_instruction_manifest`
returns the ordered, deterministic layer list.

## Provisioning jobs: retry + audit

[`jobs.py`](jobs.py) + [`model.py`](model.py) `ProvisioningJob`. A job records
one tenant's attempts; the pipeline writes step-level `AuditEvent`s into it.
`submit_and_run` runs attempt 1; a failed job (`attempt < max_attempts`) is
retryable via `retry_job` (attempt increments; a converged retry completes).
Because provisioning is all-or-nothing and idempotent, a failed attempt leaves
no partial tenant, and the failed job record **survives** the rollback — it is
audit history, not a tenant resource.

## Tenant ready-check

[`ready.py`](ready.py). `ready_check(store, rbac_store, tenant_id)` returns a
`ReadyReport` of named gates: `tenant-exists`, `tenant-active`,
`idp-mapping`, `rbac-org`, `roles-complete`, `owner-bound`, `seeds-complete`,
`defaults-present`. A later portal/health phase (#39–#42) may expose this as a
readiness endpoint; here it is the pure completeness check. Removing any
expected element flips the report to not-ready (covered by negative tests).

## Operator CLI — never anonymous HTTP

[`cli.py`](cli.py). Provisioning writes control-plane state; it is an operator
action, not an end-user one (saas-rbac doctrine: nobody can hold `roles:manage`
in a tenant that does not exist, and an anonymous write would be reachable by
anyone). Run from the repo root:

```bash
python3 -m identity.onboarding.cli provision --slug acme --name "Acme Corp" \
    --owner-email admin@acme.example.com \
    [--tenant-type platform] [--idp-tenant-id idp-acme] [--domain acme.example.com] \
    [--dry-run] [--store state.json]

python3 -m identity.onboarding.cli status  --slug acme [--store state.json]
python3 -m identity.onboarding.cli ready   --slug acme [--store state.json]
python3 -m identity.onboarding.cli jobs    --slug acme [--store state.json]
python3 -m identity.onboarding.cli retry   --slug acme [--store state.json]
python3 -m identity.onboarding.cli customize --slug acme --overlay overlay.yaml [--store ...]
python3 -m identity.onboarding.cli render  --slug acme [--store state.json]
```

State persists to `--store PATH` (or `$AO_ONBOARDING_STORE`); the RBAC view is
rebuilt from the persisted onboarding records (`materialize_rbac`), so `ready`
works in a later process. Exit codes: `0` ok, `1` provision failure,
`2` usage/validation error. There is deliberately no HTTP listener anywhere in
this lane.

## Persona registry (issue #11 — on master)

`registry/personas` (issue #11) merged to master while this lane was built, so
onboarding **installs** the tenant-starter personas from the real persona-card
registry at `registry/personas/cards/<id>.yaml`. The starter set (in
[`seeds/tenant-starter.yaml`](seeds/tenant-starter.yaml)) references
platform-default persona ids that parallel the starter profiles
(`orchestrator`, `coder`, `reviewer`) plus core SME reviewers
(`architecture-sme`, `security-sme`, `iac-sme`, `qa-sme`).

For robustness the controller still carries a *deferred-by-contract* path: on a
checkout where the persona registry directory is absent, persona seeds are
recorded as `deferred` and the ready-check counts them as satisfied. When the
registry IS present, persona seeds install normally, and an expected persona id
that cannot be found **aborts provisioning** (fail closed) — no silent skip.
Both behaviors are test-covered.

## Tests

```bash
python3 -m pytest identity/onboarding/tests -q -p no:cacheprovider
```

- `test_provision_idempotent.py` — run twice → identical end state, no
  duplicate roles/bindings/seeds.
- `test_provision_atomic.py` — all-or-nothing rollback (no partial tenant,
  existing tenants preserved), fail-before-write validation, dry-run writes
  nothing.
- `test_customization.py` — allowed overlay applies; disallowed fields,
  unknown enums, unresolved refs, system-layer inline text and duplicate
  priorities are rejected with nothing written.
- `test_jobs.py` — job status transitions, retry increments attempts and
  converges after a fix, exhaustion stops retries, failed-job audit survives
  the tenant rollback.
- `test_ready_check.py` — ready when fully provisioned (incl. persona
  deferral), and each removed element flips the matching gate to not-ready.

## Downstream consumers

| Later lane | Consumes |
|---|---|
| Identity phase 6 (#35–#38) | `provision`/job model/ready-check behind an authenticated surface; store adapter swap |
| Portal health (#39–#42) | `ready_check` as the readiness source |
| Registry service / personas (#10/#11) | seeded profile/prompt/persona records (installed from `registry/personas/cards`) |
