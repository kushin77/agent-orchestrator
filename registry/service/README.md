# registry/service — Agent Identity + Registry service

> Owner lane: **registry** (issue #10, work item 06, phase 1). Parent: EPIC-00
> (issue #4). Doctrine: [`AGENTS.md`](../../AGENTS.md),
> [`docs/EXECUTION-PLAN.md`](../../docs/EXECUTION-PLAN.md),
> [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md). Consumes the #9
> contract: [`../profiles/README.md`](../profiles/README.md) +
> [`../profiles/catalog.yaml`](../profiles/catalog.yaml) (the closed
> vocabulary + frozen AgentProfile seeds). Coordinates with
> [`../../identity/rbac/README.md`](../../identity/rbac/README.md) (issue #12:
> Org == tenant, no cross-tenant fallback).

This tree is the **multi-tenant replacement for the fleet's ad-hoc
registries**: it registers tenant-scoped agents/workers, resolves
capability→agent, keeps task-route tables, drives the agent lifecycle, and
issues per-agent session credentials with scoped claims (tenant, agent, role,
allowed tools). The companion audit log lives in
[`../events/README.md`](../events/README.md) (`registry/events`, issue #10).

## The model

```
tenant (the agent org) ── owns ──> Agent rows        (one per worker instance)
                                 ── owns ──> TaskRoutes (task type -> candidates)
                                 ── owns ──> Sessions    (scoped claims identity)
```

- **Tenant-scoped everywhere.** An agent, route, capability or identity lookup
  in one tenant never observes another tenant's rows and never falls back
  across a tenant boundary (the no-cross-tenant doctrine, also enforced by
  rbac #12 and the tenant-scoped MCP pattern).
- **Agent row** (`Agent`): `agentId`, `tenantId`, `profileRef`, `profileVersion`,
  `owner`, `capabilities`, `tools`, `modelTier`, `role`, `status`, `lastSeen`,
  `registeredAt`. An Agent is created **from a frozen AgentProfile** (issue #9)
  — its `capabilities` are the profile's closed `capabilitySet` and its `tools`
  the profile's closed `toolAllowlist`, both validated against
  [`catalog.yaml`](../profiles/catalog.yaml) at registration. The service
  *consumes* the profile ids (`coder`, `reviewer`, `orchestrator`,
  `researcher`, `data-agent`) and never redefines them.
- **Lifecycle** (closed status vocabulary, enforced transitions):

  ```
  registered --activate--> active --pause--> paused
      |                       |               |
      +---------- retire -----+---------------+--> retired (terminal)
  ```

  A fresh agent is `registered` (not yet dispatchable); `activate` makes it
  dispatchable; `retire` is terminal and can never be re-activated. Every
  transition appends to the append-only audit log.

## What the service does

| Capability | API | Notes |
|---|---|---|
| Register agents | `registry.register(tenant_id, agent_id, profile_ref)` | From a #9 profile seed; unknown profile/capability/tool refused (fail closed). |
| Lifecycle | `registry.activate / pause / retire / touch` | State-machine enforced; terminal `retired` cannot be re-activated. |
| Reads | `registry.get / list / records` | Strictly tenant-scoped; the same agent id in another tenant is invisible. |
| Task routes | `registry.set_task_route(...)` / `registry.list_routes` | Route may only name agents registered in the same tenant; required capabilities must be held by every candidate and come from the closed catalog. |
| Task resolution | `registry.resolve_task(tenant_id, task_type)` | **Fails closed**: an unknown task type is denied (`UnknownTaskTypeError`) — never routed, never falling back to other task types or other tenants. Returns only `active` candidates; paused/retired are skipped, not substituted. |
| Capability resolution | `registry.resolve_by_capability(tenant_id, capability)` | Active agents of that tenant holding the capability; unknown capability refused. |
| Identity issuance | `registry.issue_session(...)` → `AgentSession` | HMAC-SHA256 signed token with scoped claims: `tenantId`, `agentId`, `role`, `allowedTools` (the agent's tool allowlist). Only `active` agents are issued sessions. |
| Session use | `registry.verify_session(token)` / `require_scope(session, tenant)` / `authorize_tool_use(...)` | Signature+expiry verified; a session minted for tenant A is refused for tenant B (`CrossTenantDenied`) — no cross-tenant fallback. |

### Scoped-claims identity (no cross-tenant fallback)

`issue_session(tenant_id, agent_id, ...)` mints a session whose claims are
scoped to exactly one tenant. The two enforcement points that make cross-tenant
use impossible:

1. **Issuance** looks the agent up **only in the requested tenant**
   (`store.require_agent`). An agent of tenant A does not exist in tenant B's
   view, so it can never obtain a session claiming tenant B
   (`UnknownAgentError`, negative-tested).
2. **Use** refuses any session whose `tenantId` does not match the tenant it is
   being used in (`require_scope` → `CrossTenantDenied`), and
   `authorize_tool_use` composes scope-then-tool in that order (the
   capital-underwriting tenant-scoped MCP pattern). A forged claim never
   verifies (HMAC signature).

Identity *issuance* is this service's job; *authorization* (may this role
perform `resource:action`?) stays with the rbac engine
([`identity/rbac`](../../identity/rbac/README.md), issue #12). The session's
`role`/`allowedTools` claims are the snapshot a model gateway or guardrail
consumes; rbac re-resolves live bindings on every call.

### Audit trail

Every mutating call appends to the append-only, hash-chained event log
(`register`, `activate`, `pause`, `retire`, `route`, `session`) — see
[`../events/README.md`](../events/README.md). The log refuses a tampered or
malformed record, so "who did what in which tenant" is mechanically verifiable.

## Layout

| Path | Purpose |
|---|---|
| [`errors.py`](errors.py) | Every failure type (cross-tenant denial, unknown task type, invalid transition, ...). |
| [`model.py`](model.py) | `Agent` row + lifecycle status vocabulary + transitions. |
| [`catalog.py`](catalog.py) | Closed-vocabulary loader + profile-seed resolver (consumes #9; read-only). |
| [`store.py`](store.py) | Tenant-scoped in-memory persistence seam. |
| [`registry.py`](registry.py) | `AgentRegistry` facade: lifecycle + routing + identity. |
| [`routing.py`](routing.py) | `TaskRouter`: task-route table + fail-closed task/capability resolution. |
| [`identity.py`](identity.py) | `IdentityService` / `AgentSession`: scoped-claims issuance + verification. |
| [`__init__.py`](__init__.py) | Public API re-exports. |
| [`tests/`](tests/) | pytest suite (lifecycle, routing, scoped-claims identity, audit wiring). |

## Usage

```python
import sys
sys.path.insert(0, "registry")          # registry/ has no __init__.py yet
from service import AgentRegistry
from events import open_event_log

log = open_event_log("registry/events/audit.jsonl")  # append-only file
reg = AgentRegistry(event_log=log)

# 1. register agents from frozen profiles (issue #9 seeds)
reg.register("acme", "worker-1", "coder")            # status: registered
reg.register("acme", "reviewer-1", "reviewer")
reg.activate("acme", "worker-1")
reg.activate("acme", "reviewer-1")

# 2. task routes + fail-closed resolution
reg.set_task_route("acme", "code-review", ["reviewer-1"],
                   required_capabilities=["code-review"])
route = reg.resolve_task("acme", "code-review")      # RouteResolution
coders = reg.resolve_by_capability("acme", "code-author")

# 3. scoped-claims session identity
session = reg.issue_session("acme", "worker-1", signing_key=b"runtime-key")
token = session.encode(b"runtime-key")               # HMAC-signed, tenant-scoped
reg.require_scope(session, "acme")                   # ok
# reg.require_scope(session, "globex")               # -> CrossTenantDenied
```

## Importing and running the tests

`registry/service` is a self-contained package, importable as `service` when
`registry/` is on `sys.path` (the tests arrange this in `tests/conftest.py`);
the `registry/events` sibling is importable as `events` the same way:

```bash
# from the repo root
python3 -m pytest registry/service/tests registry/events/tests -q -p no:cacheprovider
make verify   # repo gate must stay green (YAML/JSON parse, docs, markers, secrets)
```

## Verification summary (issue #10)

- `python3 -m pytest registry/service/tests registry/events/tests -q` → green
  (see the PR evidence), covering: the register/activate/pause/retire lifecycle
  + invalid transitions; tenant-scoped reads; task routing (route by
  capability, unknown task types denied, cross-tenant route refs refused);
  scoped-claims identity incl. the cross-tenant negatives; and the append-only
  audit log (tamper detection) that every registry operation writes to.
- `make verify` → green.

## Provenance (cannibalized and adapted)

Adapted from the sources indexed in
[`docs/CANNIBALIZATION.md`](../../docs/CANNIBALIZATION.md); each was read
through the `.research/` read-only mirrors (GR-10), not copied verbatim.

| Source | Pattern adapted | Where it landed |
|---|---|---|
| `hermes-agents` `services/capability_registry.py` + `models/` + `api/capabilities.py` | Agent catalog (id/name/capabilities/health), task-route table, resolve-by-capability REST shape | `Agent` row, `TaskRouter`/`set_task_route`/`resolve_task`; hermes' fallback-to-any-healthy-agent was deliberately **dropped** (issue #10 fail-closed) |
| `leaderboard` `lib/leaderboard-registry.sh` + `lib/fleet-identity.sh` + `data/registry/` | Registry-row discipline, identity fields (repo/role/session/started), fail-closed "refuse rather than mislabel" | Tenant-scoped Agent row, `registered_at`/`last_seen`, fail-closed validation |
| `shared-governance` `GLOBAL_STANDARDS/agent-identity.md` + `schemas/agent-identity-jwt.schema.json` | Agent action records are append-only events; JWT claims (iss/sub/aud/iat/exp/jti/agent_id) | `AgentSession` claim shape + HS256 token; event kinds |
| `CMR` `registry/events/` (append-only log) | Append-only JSON Lines event log + closed event enum | `registry/events` audit log |
| `capital-underwriting` `mcp/capitalMcpServer.ts` | Tenant-scoped identity enforcement on every tool call; no scope never means another tenant | `require_scope` + `authorize_tool_use` scope-then-tool gate |
| rbac (issue #12, `identity/rbac`) | Org == tenant; no cross-tenant fallback; session role snapshot | Coordination (see above) |
