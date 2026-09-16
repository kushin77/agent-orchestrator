# identity/rbac — Agent Org RBAC / role model

The platform's authorization contract (issue #12, work item 08): **Org → Team →
Agent** role model with `resource:action` permissions, where **the scope gate
is separate from the permission gate**. This is the enforcement core that every
later phase consumes — identity phase 6 (#35–#38) and every guardrail
middleware that gates tool/API calls.

> **Contract freeze.** Per the execution-plan doctrine, whichever lane lands a
> shared contract first owns it and the rest consume the field/function names,
> never the files. This subtree is that contract for RBAC: later phases should
> import from `identity.rbac` (or the `rbac` package) and extend their own
> pillars rather than redefining roles, permissions, or the two-gate flow here.

## Provenance (cannibalization)

Adapted to Python from (read-only fleet/hub sources, per the cannibalization
index in `docs/CANNIBALIZATION.md`):

- `saas-rbac` `services/backend-api/src/rbac/{types,resolve,guard,bindings,presets}.ts`
  + `prisma/schema.prisma` — the `resource:action` language, union resolution,
  the two-gate 403 guard, the no-lockout invariant, preset packs.
- `shared-governance` `governance/multi-tenancy/rbac_engine.py` +
  `governance/authorization-system/` — Python RBAC patterns, tenant scoping.
- hub `CMR` `controller/teams.tsv` + `docs/IDENTITY.md` — role doctrine as
  data, least-privilege grants, declarative identity.
- `leaderboard` `config/platoons.yaml` + `docker/worker-fleet/personas.yaml` —
  role-key conventions.

The TypeScript was not copied verbatim; it was ported to Python with the same
semantics, tightened around the two-gate doctrine.

## The model

```
Org (the tenant)  ->  Team  ->  Agent
```

- **Org** *is* the tenant — the "agent org" a tenant defines (EPIC-00). It owns
  roles, teams and agents.
- **Team** — the unit a team-scoped role is granted to.
- **Agent** — a managed commercial agent (Claude / DeepSeek / …), always under
  exactly one Team.

Entities live in [`model.py`](model.py) as frozen dataclasses:
`Org`, `Team`, `Agent`, `ScopeNode`, `Role`, `Binding`, `Session`.

### Permission strings

A permission is `resource:action` with one reserved wildcard segment `*`:

| Grant | Matches |
|-------|---------|
| `agent:run` | exactly `agent:run` |
| `agent:*` | every action on `agent` |
| `*:run` | `run` on every resource |
| `*:*` | everything (Owner preset only) |

Only the *granted* side may carry a wildcard; a request is always a concrete
pair. A permission is only ever evaluated **inside one Org** — nothing is
global. Helpers: `format_permission`, `is_permission`, `split_permission`,
`permission_granted`, `role_grants` (all in `model.py`).

The catalog is open: presets and custom roles may introduce further
`resource:action` strings (see [`presets/`](presets/)). The engine itself only
hard-depends on a handful of canonical strings (`PERMISSIONS` in `model.py`),
notably `agent:run` and the role-admin permission `roles:manage`.

## The two gates — scope is separate from permission

This is the platform's central RBAC doctrine (the cross-tenant read incident
doctrine). Two distinct questions, two distinct functions, in
[`resolve.py`](resolve.py):

1. **`resolve_scope(store, subject, node)` → `ScopeResolution`** — the **scope
   gate**: *is this Org/Team/Agent node inside the subject's reachable org
   tree at all?* An org-wide binding reaches every node of the Org (including
   org-level administration); a team-scoped binding reaches that team and its
   agents — and nothing else. A subject with **no binding in the requested
   Org** is out of scope, no matter what its role strings would otherwise
   grant.
2. **`authorize(store, subject, resolution, permission)` → bool** — the
   **permission gate**: given an *already-resolved* scope, does the union of
   the roles covering that node grant the requested `resource:action`?

They are composed by **`guard(...)`** (in [`guard.py`](guard.py)) in the only
safe order — scope first, permission second:

```python
resolution = resolve_scope(store, subject, node)   # gate 1: can they act HERE?
if not resolution.ok:
    return Decision(allowed=False, reason="scope", ...)   # never reaches gate 2
if not authorize(store, subject, resolution, permission): # gate 2: can they do THIS?
    return Decision(allowed=False, reason="permission", ...)
return Decision(allowed=True, ...)
```

A principal with a permission can **never** apply it outside its resolved
scope: when the scope gate fails it returns a `Decision` with
`reason == "scope"` and the permission gate is never reached. There is **no
cross-tenant fallback and no cross-team fallback** — even if the other
Org/team defines an identically named role granting the same permission. This
is enforced by the negative tests in
[`tests/test_scope_vs_permission.py`](tests/test_scope_vs_permission.py).

### `Decision`

Every guard call returns a `Decision`:

- `allowed` — whether the call may proceed.
- `reason` — `"scope"` (the subject cannot act in the node at all),
  `"permission"` (in scope, but the permission is not held), or `None` when
  allowed.
- `code` — machine reason for scope denials (`unknown_org`, `unknown_team`,
  `unknown_agent`, `out_of_scope`) or `"denied"` for permission denials.
- `permission` / `required_permissions` / `missing_permissions` — what was
  asked and what was missing.

## No-lockout invariant

[`bindings.py`](bindings.py) implements `grant_role` / `revoke_role`. Both are
idempotent (re-grant returns the existing binding; revoking an unheld role is a
no-op). **Revocation refuses to remove an Org's last holder of the Org's
role-admin permission** — raising `LastAdministratorError` rather than locking
everyone out:

- The permission is `Org.role_admin_permission`, default `roles:manage`, and is
  **keyed to the Org**, not hardcoded (saas-rbac #125: a tenant whose pack
  speaks its own vocabulary must be protected by *that* permission).
- Wildcards count: `*:*` confers role administration (an Owner-only tenant is
  the common case).
- The check runs before the delete, so a refusal removes nothing.

Tests: [`tests/test_no_lockout.py`](tests/test_no_lockout.py).

## Preset role packs

[`presets/`](presets/) holds YAML role packs. Four are per **tenant type**
(what `seed_org` resolves by default):

| Pack | tenant_type | Roles |
|------|-------------|-------|
| [`platform.yaml`](presets/platform.yaml) | `platform` | owner, admin, team-admin, agent-operator, member, viewer |
| [`startup.yaml`](presets/startup.yaml) | `startup` | owner, admin, member |
| [`smb.yaml`](presets/smb.yaml) | `smb` | owner, admin, team-admin, member |
| [`enterprise.yaml`](presets/enterprise.yaml) | `enterprise` | owner, admin, team-admin, agent-operator, member, viewer, auditor |

Plus one **named** pack — loaded by name, deliberately *not* a tenant type, so
the four above keep their exact role mixes:

| Pack | key | Roles |
|------|-----|-------|
| [`csuite.yaml`](presets/csuite.yaml) | `csuite` | ceo, cto, coo, cfo, cmo (issue #638) |
| [`head-agents.yaml`](presets/head-agents.yaml) | `head-agents` | hermes-head, paperclip-head (issue #952, opt-in per tenant) |

`load_pack(key)` accepts any `<key>.yaml` beside the module; `available_packs()`
lists them; `load_csuite_pack()` loads the executive pack.

Pack schema (see the YAML files):

```yaml
key: platform                 # pack key == tenant_type
name: Platform Default
role_admin_permission: roles:manage   # what confers role admin in this pack
roles:
  - key: owner                # stable machine key
    name: Owner
    level: org                # org | team (team-level roles cannot be org-wide)
    permissions: ["*:*"]
    is_system: true
```

**Custom-pack seam:** a tenant that brings its own RBAC model supplies a custom
pack through `parse_pack(yaml_text)` (validates the schema) and
`register_pack(pack)`; `seed_org(store, org)` then resolves the pack for the
Org's `tenant_type` and seeds its roles. Seeding refuses a pack that cannot
administer the Org — every Org must be born with a role that grants its
role-admin permission. Loader: [`presets/__init__.py`](presets/__init__.py);
tests: [`tests/test_presets.py`](tests/test_presets.py).

## Sessions and the guard middleware contract

Agent sessions carry roles (`Session.roles` in `model.py`). The guard is the
**enforcement core**; real HTTP middleware arrives in later phases and must
compose it, never replace it:

```python
session = start_agent_session(store, subject, agent_id)  # requires agent:run in scope
decision = guard_session(store, session, "tool:call")     # every tool/API call
```

Middleware contract (documented in full in the `guard.py` module docstring):

1. Every tool/API call that acts on a resource **must** pass through `guard`,
   `guard_required`, or `guard_session` before acting. No action is unguarded.
2. A denial is an unconditional stop. A later HTTP layer maps it to **403**
   with the `Decision` fields — never to a fallback that re-tries in another
   scope.
3. Never skip the scope gate, and never conflate a `"scope"` denial with a
   `"permission"` denial: the fix for one is not the fix for the other.
4. Emit denials via `authorization_denied_payload(decision)` so observability
   (phase 5) can count them. Reserved fields — `event`, `orgId`/`tenantId`,
   `permission` — mirror the saas-rbac `rbac/authorization_denials` metric
   contract and must not be renamed. Scope denials report the pseudo-permission
   `SCOPE_DENIAL_PERMISSION` (`scope:resolve`) so a spike is attributable.
5. A session carries its roles as a **snapshot for audit/context**.
   Enforcement re-resolves live bindings from the store on every call, so a
   revocation takes effect immediately (there is deliberately no caching: the
   dangerous cache failure mode — a revoked subject keeping access — produces
   fewer denials and is invisible to monitoring).

Tests: [`tests/test_guard_sessions.py`](tests/test_guard_sessions.py).

## C-suite role boundaries (issue #638)

The two gates answer *what a principal may do*. [`boundaries.py`](boundaries.py)
answers the complementary workbook question: *what is outside a seat's
boundary*, so leaving it is **refused** rather than merely un-granted.

A boundary is **derived, never restated**: `load_csuite_boundaries()` reads the
landed persona cards `registry/personas/cards/{ceo,cto,coo,cfo,cmo}.yaml`
(issue #632, read-only) plus `org-chart.yaml`, and cross-checks that the chart
and the cards agree on each seat's reporting edge and monthly cap. The three
boundary axes are the card's own fields:

| Axis | Card field | Refusal |
|------|-----------|---------|
| lanes | `ownedLanes` | `"lanes"` — the target lane is not the seat's |
| tools | `toolAllowlist` | `"tools"` — the tool is outside the seat's allowlist |
| capability | `capabilitySet` | `"capabilities"` — the seat does not hold it |
| budget | `monthlyBudgetCapUsd` (+ `guardrailPolicyRef`) | `"budget"` — spend would exceed the cap |

```python
pack = load_csuite_boundaries()                       # 5 seats, from the cards
decision = guard_boundary(pack, "cfo", BoundaryAction(lane="marketing"))
assert decision.refused and decision.axis == "lanes"  # refused, naming the axis
```

A refusal names the offender (lane / tool / capability / amount) and the seat's
own boundary on that axis, so a gate can quote the reason. An unreadable
boundary — a card missing a boundary field, or a chart disagreeing with a card —
is a loud error at load time, never a silently unbounded seat.

The RBAC **preset pack** for the same five seats is
[`presets/csuite.yaml`](presets/csuite.yaml) (`load_csuite_pack()`); its
permissions are the RBAC projection of the same cards. Pack key `csuite` is a
*named* pack, deliberately **not** a tenant type, so the four built-in packs
keep their exact role mixes. Only the CEO grants `roles:manage` — the executive
authority the no-lockout invariant protects.

Tests: [`tests/test_csuite_boundaries.py`](tests/test_csuite_boundaries.py).

## Org-wide skill sharing and cross-tenant isolation (issue #638)

The skills adapter (`integrations/paperclip/adapters/skills/`) declares *which*
`SKILL.md` files load; it deliberately carries no tenancy. [`skills.py`](skills.py)
supplies the audience half:

- **Platform** skills are owned by `PLATFORM_ORG` (`platform`) and shared
  **read-only** with every org — visible to all, modifiable by none, and never
  shadowable (a tenant registering the same id is refused).
- **Tenant** skills are owned by exactly one Org and are **never visible to
  another tenant** — no wildcard, no fallback.
- **Org-scoped shares** (`share_org_scope`) are **explicit**: only the owner may
  share, and every target org is named. A wildcard (`"*"`) target is refused
  (`CrossTenantShareError`) rather than silently widened to every tenant.

Visibility is *computed* (`visible_skills(viewer_org)` / `invisible_skills`),
never a stored mutable flag — the dangerous failure mode of a stored field is
that it silently drifts from the shares that justify it, the same reasoning that
makes the guard re-resolve live bindings instead of trusting a session snapshot.

```python
reg = SkillShareRegistry()
reg.register_platform("mcp-tool-projection")           # visible to every org
reg.register_tenant("acme-onboarding", owner_org="acme")
assert "acme-onboarding" not in {s.id for s in reg.visible_skills("globex")}
```

Tests: [`tests/test_skills_isolation.py`](tests/test_skills_isolation.py).

## Head-of-org agent bindings (issue #952)

`hermes` (`registry/personas/cards/hermes.yaml`, coding/capability-routing
worker) had no tenant/RBAC binding anywhere in `identity/` — the only `hermes`
hits in this subtree were `hermes-agents` provenance notes (where a *pattern*
was cannibalized from), not an in-repo binding. The `paperclip` "carve-out" in
[`skills.py`](skills.py) is **not** a role binding either — it scopes
`SKILL.md` visibility per org and creates no `Role`/`Binding` row at all.
[`head_bindings.py`](head_bindings.py) supplies the real thing, for both
personas, on one pattern.

**The role.** [`presets/head-agents.yaml`](presets/head-agents.yaml) declares
`hermes-head` and `paperclip-head`: org-wide roles that may read the org/agent
registry (`org:read`, `team:read`, `agent:read`) and create their own kind of
work item (`directive:create`/`ticket:create` for hermes,
`doc:create`/`knowledge:read` for paperclip). Neither role is ever granted
`org:manage` (tenant administration), `secret:rotate`, `rollout:approve` or
`roles:manage` — `head_bindings.FORBIDDEN_PERMISSIONS` names all four and a
test asserts the pack never grants one. A cross-tenant read is refused by a
different mechanism entirely — the scope gate (`resolve_scope`), since a
`Binding` never crosses an Org (see `model.Binding`), the same guarantee every
other subject in this package gets.

**Not a tenant type, opt-in only (GR-28).** `head-agents` is never resolved by
`org.tenant_type` (`presets.BUILTIN_TENANT_TYPES` does not name it) and no
code in this repo seeds it automatically — an Org seeded from any built-in
pack (or `csuite`) carries **no** head-of-org binding until a tenant calls
`bind_persona_to_tenant(store, org, persona_id)` for the persona it wants,
exactly once per persona, idempotently. `unbind_persona_from_tenant` revokes
it; `is_persona_bound` / `guard_persona` read and enforce it.

```python
from rbac import HERMES_PERSONA_ID, InMemoryStore, bind_persona_to_tenant, guard_persona, seed_org

store = InMemoryStore()
org = store.add_org("acme", "Acme", tenant_type="startup")
seed_org(store, org)  # the tenant's own admin/owner/member roles - untouched

# Unbound: refused at the scope gate, GR-28's default.
assert guard_persona(store, org.id, HERMES_PERSONA_ID, "org:read").denied

bind_persona_to_tenant(store, org, HERMES_PERSONA_ID)     # explicit opt-in
assert guard_persona(store, org.id, HERMES_PERSONA_ID, "org:read").allowed
assert guard_persona(store, org.id, HERMES_PERSONA_ID, "org:manage").denied  # never granted
```

Tests: [`tests/test_head_bindings.py`](tests/test_head_bindings.py) — allowed
ops pass and forbidden ops are refused by name for a bound persona; an unbound
tenant is refused; a binding never reaches a second tenant (cross-tenant read
refused); bind/unbind are idempotent; and deleting the `Binding` row directly
reproduces the gate script's negative control. Gate:
[`scripts/check-rbac-head-binding.sh`](../../scripts/check-rbac-head-binding.sh)
(registered in `scripts/verify.sh`) re-runs the suite, asserts GR-28
default-off live, and provokes the same delete-the-binding control end to end.

## Layout

| File | Purpose |
|------|---------|
| [`model.py`](model.py) | Data types + permission language (pure) |
| [`store.py`](store.py) | In-memory persistence seam (a later phase may back it with a DB adapter) |
| [`resolve.py`](resolve.py) | The two gates: `resolve_scope` and `authorize` |
| [`bindings.py`](bindings.py) | `grant_role` / `revoke_role` + the no-lockout invariant |
| [`guard.py`](guard.py) | `guard` / `guard_session` / sessions + denial contract |
| [`presets/`](presets/) | YAML role packs + custom-pack seam |
| [`head_bindings.py`](head_bindings.py) | Tenant/RBAC binding for the head-of-org personas (issue #952), opt-in per tenant |
| [`tests/`](tests/) | pytest suite (see below) |
| [`__init__.py`](__init__.py) | Public API re-exports |

## Importing and running the tests

`identity/rbac` is a self-contained package. It is importable as `rbac` when
`identity/` is on `sys.path` (the tests arrange this in
[`tests/conftest.py`](tests/conftest.py)) and as `identity.rbac` once a later
identity-phase lane adds an `identity/__init__.py`.

```bash
# from the repo root
python3 -m pytest identity/rbac/tests -q -p no:cacheprovider
make verify   # repo gate must stay green (YAML/JSON parse, docs, no markers)
```

## Verification summary (2026-09-08)

- `python3 -m pytest identity/rbac/tests -q -p no:cacheprovider` → **52 passed**,
  covering: permission allow/deny incl. wildcards; the scope-vs-permission
  separation negative tests (cross-tenant and cross-team denied at the scope
  gate); the no-lockout invariant; preset-pack loading + custom-pack seam;
  guard + sessions.
- `make verify` → green (see PR evidence).
