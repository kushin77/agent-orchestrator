# identity/entitlements — plan → entitlement → override tiering

The **capability-tiering contract** for the platform (issue #36, work item 32;
parent EPIC-00 issue #4). Owner lane: **identity** — this subtree is
`identity/entitlements/**` only. Doctrine: [`AGENTS.md`](../../AGENTS.md),
[`docs/EXECUTION-PLAN.md`](../../docs/EXECUTION-PLAN.md),
[`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md),
[`docs/GOLDEN-RULES.md`](../../docs/GOLDEN-RULES.md).

## What this is

A tenant's **SUBSCRIPTION PLAN** maps to a set of **ENTITLEMENTS** (feature
flags and numeric limit allowances); each entitlement maps to a set of RBAC
permission grants; and an **OVERRIDE** grants an entitlement above the plan
within governance (audited, time-boxed, requires authority):

```
Plan  --(toggles)-->  Entitlements (features + limits)
                              |
                              |  feature grants (RBAC permissions)
                              v
          Entitlement -> RBAC mapping (which permissions a capability unlocks)
                              |
                              v
   effective permissions = f(plan entitlements ∩ org roles)   [+ overrides]
```

The RBAC core that this sits on — `resource:action` permissions, Org → Team →
Agent, the scope/permission two-gate doctrine, preset role packs per tenant
type, the no-lockout invariant — is the **frozen contract** of
[`identity/rbac`](../rbac/README.md) (issue #12). This subtree consumes that
vocabulary and never redefines it. `Org.id == Tenant.id` (an Org *is* the
tenant, [`identity/onboarding`](../onboarding/README.md), issue #14).

## Provenance (cannibalization)

Adapted to Python from (read-only fleet/hub sources, per the cannibalization
index in [`docs/CANNIBALIZATION.md`](../../docs/CANNIBALIZATION.md)):

- `saas-rbac`
  `services/backend-api/src/billing/{entitlements,overrides,verify-subscription,types}.ts`
  — the plan catalog that lives with the code; the **whole-answer override**
  (an override states the entire answer for its feature and is never a patch);
  the active-subscription gate kept **separate** from RBAC/scope; unknown plan
  resolves least-privilege (here: explicit fail-closed codes).
- `saas-rbac` `services/backend-api/src/rbac/*` + `prisma/schema.prisma` — the
  permission language and two-gate guard consumed unchanged from
  `identity/rbac`.
- `shared-governance`
  `governance/authorization-system/{rbac,abac,approval_workflows,time_restrictions}.py`
  — RBAC/ABAC and approval/time restriction patterns behind the override
  authority + time-box.
- `capital-underwriting`
  `apps/server/src/middleware/{planGate,requireFeature}.ts` — feature-flag
  resolution order and the "explicit override > defaults > deny" doctrine.
- hub `CMR` `guardrails/policy/controls.yaml` + `docs/IDENTITY.md` —
  entitlement-style toggles and identity doctrine (PATTERN-ONLY).

No TypeScript was copied verbatim; the semantics were ported to Python with
the same intent, tightened around fail-closed evaluation.

## Consumed contracts (field names frozen upstream)

| Upstream | Consumed |
|---|---|
| `identity/rbac` (issue #12) | `Org`/`Role`/`Binding`, `resource:action` permissions, `ScopeNode`, `resolve_scope` / `authorize` / `effective_permissions`, `permission_granted`, preset packs per `tenant_type`, `seed_org`, `grant_role` |
| `identity/onboarding` (issue #14) | `Tenant.id == Org.id`; `tenant_type` vocabulary; provisioning creates the RBAC org this contract tiers |

## Tree layout

```text
identity/entitlements/
├── README.md                # this contract doc
├── model.py                 # closed vocabularies + pure data types
├── catalog.py               # plan-catalog parse/validation (fail closed)
├── plans/
│   └── catalog.yaml         # the shipped catalog: features (grants) + plans
├── store.py                 # InMemoryStore (profiles, overrides, audit log)
├── engine.py                # assign_plan + feature/evaluation semantics
├── overrides.py             # authority-gated, time-boxed, audited overrides
├── errors.py                # entitlement-contract refusal exceptions
└── tests/                   # pytest suite (offline): catalog, effective
                             #   access, overrides, feature gate, audit
```

## The model

Pure data types in [`model.py`](model.py), validated by
[`catalog.py`](catalog.py), persisted by [`store.py`](store.py):

- **`FeatureDef`** — a catalog feature: `kind` (`feature` = on/off capability,
  `limit` = metered capacity), description, and `grants` — the concrete
  `resource:action` permissions it unlocks (empty for a pure surface flag like
  `api_access`/`sso`). This is the **entitlement → RBAC mapping**.
- **`Plan`** / **`PlanEntitlement`** — a commercial tier and its toggles
  (`enabled`, plus `limit` for limit features; `None` = unlimited).
- **`PlanCatalog`** — an immutable, validated catalog (features + plans).
- **`EntitlementProfile`** — a tenant's plan assignment and
  `subscription_status` (`active`/`inactive`).
- **`Override`** — a time-boxed, whole-answer departure for one feature
  (`enabled`, optional `limit`, `expires_at`, `note`, `granted_by`).
- **`FeatureState`** — resolved state of one feature for an org, with a
  fail-closed `code` when it could not be resolved.
- **`AccessDecision`** — outcome of one effective-access check, with the
  `reason`/`code` of the gate that refused.
- **`EntitlementAuditEvent`** — one immutable audit record of a plan/override
  change.

### The ungated core (self-administration floor)

`CORE_UNGATED_PERMISSIONS` (model.py) is the set of permissions that
administer and operate the org itself and that **a plan can never strip**:
`org:*`, `roles:read`/`roles:manage` (the no-lockout key), `member:read`,
`team:read`, `agent:read`/`agent:run`, `session:*`,
`tool:call`, `prompt:read`, and the override-authority permission itself.
Without this floor a plan downgrade could lock a tenant out of its own org.
Everything beyond the core is a **plan-gated capability** unlocked by an
entitled feature.

## The catalog

[`plans/catalog.yaml`](plans/catalog.yaml) declares the feature registry
(each feature's grants) and the plans (`free`, `pro`, `enterprise`). Features
are product configuration and live with the code that enforces them (saas-rbac
doctrine): adding a capability needs no migration, only a reviewed change to
this file. Validation in [`catalog.py`](catalog.py) is strict and fail-closed:
unknown feature references, duplicate keys, malformed grants, limits on
non-limit features, and empty plans all refuse to load.

## Evaluation semantics

### The three + one gates (issue #36 AC 4)

[`engine.py`](engine.py) `evaluate_permission(...)` composes four distinct,
attributable gates, **never raising for a deny**:

1. **Subscription gate** — org profile/plan resolve and the subscription is
   active, else `reason="subscription"` with code `no_plan` / `unknown_plan` /
   `subscription_inactive`. This gate is **separate from RBAC scope and
   permissions**: an inactive subscription grants nothing even for a core
   permission a subject's roles and scope fully cover.
2. **Scope gate** — RBAC `resolve_scope`: can the subject act in the requested
   Org/Team/Agent node at all?
3. **Permission gate** — RBAC `authorize`: do the subject's roles grant the
   requested `resource:action` in that resolved scope?
4. **Entitlement gate** — the requested permission is either in the ungated
   core or unlocked by an entitled feature; otherwise
   `reason="entitlement"`, `code="not_entitled"`. This is the capability
   tiering: **a plan caps even a `*:*` Owner**, and a wildcard role grant
   cannot reach beyond what the plan unlocks.

A subject's **effective permissions** are therefore
`f(plan entitlements ∩ org roles)`: a plan-gated permission is only effective
when *both* the role and the plan grant it — a free-plan admin holding
`budget:manage` by role is still denied (entitlement), and a pro-plan member
whose role lacks it is denied (permission).

`feature_state` / `feature_enabled` / `limit_for` expose the pure plan+override
answer for one feature (used by the active-subscription feature gate);
`entitled_permissions` returns the org's entitled permission set. **Fail
closed everywhere:** unknown org/no plan → `no_plan`, unknown plan →
`unknown_plan`, unknown feature → `unknown_feature` — never a silent grant and
never a least-privilege guess.

### Plan assignment

`assign_plan(store, catalog, org_id, plan_key, *, actor, subscription_status,
note, now)` records the tenant's plan (audited). Refuses an unknown plan
(fail closed) or an empty actor. Plan changes **never touch existing
overrides** — see downgrade-safety below.

## The override tier

[`overrides.py`](overrides.py) implements the escape hatch the plan gate is
deliberately built to need (saas-rbac `overrides.ts`): a negotiated raise
above plan, a temporary limit bump, a grandfathered capability. Governed by
three invariants:

- **Requires authority** — granting *and* revoking require the grantor to hold
  the override-authority permission (`entitlement:override`) in the org, as
  evaluated through the RBAC contract (scope gate first; wildcards count). A
  member or an out-of-scope subject is refused with
  `OverrideAuthorityError` — **an unexpired override beyond authority is
  denied**. The built-in preset packs confer the authority on `owner` via
  `*:*`; a tenant with a custom pack grants it to whichever role it chooses
  (the custom-pack seam, issue #12).
- **Time-boxed** — an override must name a future `expires_at`; there are no
  permanent overrides (`OverrideExpiryError` otherwise). It is only honored
  while unexpired — **an expired override no longer grants** anything.
- **Audited** — every grant and revoke appends an immutable
  `EntitlementAuditEvent` naming who acted, on what, and until when.

An override is the **whole answer** for its feature: `enabled: false` is a
real revocation of what the plan grants, and it never inherits from the plan —
so a plan change underneath it cannot silently shift a negotiated agreement
(**downgrade-safe**: a downgrade does not cancel a granted override; the
override holds until it expires, then the (lower) plan value takes effect).
Only one active override may exist per (org, feature); a re-grant supersedes
the previous one (and records it in the audit detail). An override may only
name a feature declared in the catalog (fail closed), and a numeric limit is
only legal on a limit-kind feature.

## Auditing

Every `assign_plan`, `grant_override` and `revoke_override` appends an
append-only, per-org `EntitlementAuditEvent` (`store.events_for_org`). The
audit trail is the record of record for plan/override changes; a later
observability phase (control plane) ships it to the tenant ledger.

## Fail-closed doctrine (security-sensitive lane)

- A plan is never implied: a tenant with no `assign_plan` is `no_plan`.
- Unknown plan or unknown feature is a coded denial, never a silent grant and
  never a least-privilege fallback that could later flip to a grant.
- The entitlement gate refuses before any action; denials carry a machine
  `reason`/`code` so a later HTTP layer maps them to 403s with cause
  (`subscription`/`scope`/`permission`/`entitlement`) and observability can
  attribute spikes.
- Overrides are the only path above a plan, and they are authority-gated,
  time-boxed and audited — there is no other raise.

## Tests

[`tests/`](tests/) is a pytest suite (offline, no network). Run it in
isolation from the repo root (same convention as every suite):

```bash
python3 -m pytest -q identity/entitlements/tests
```

Coverage: catalog parse/validation negatives, effective-access tiering
(cap-caps-Owner, intersection needs role and plan, scope-before-entitlement,
fail-closed unknown/no plan), the override tier (authority denial, time-box,
expired-override, whole-answer revocation, downgrade-safety, supersession),
the pure feature/subscription gate (separate from RBAC), and audit of plan and
override changes.

## Integration seam

A later control-plane/portal phase (issue #35+) calls `assign_plan` when a
tenant's subscription is known and evaluates every tool/API call through
`evaluate_permission` — the same path for console users and agent subjects.
Until a plan is assigned, a tenant is fail-closed (`no_plan`). The
`InMemoryStore` here is the offline seam; a database adapter swaps in behind
the same accessors.
