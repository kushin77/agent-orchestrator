# Live data bridge (`ao.bridge/v1`)

The **versioned read transport** over the platform's four state families —
`registry`, `gateway`, `telemetry`, `guardrails` — plus an SSE change signal.
Issue #339; the cross-cutting enabler for every pane-of-glass surface.

The data already exists on this checkout; what did not exist was **one stable,
versioned, authenticated address** for it and **one change signal** that says
"the platform's state moved". This document is the contract for that address.

## Consumers

**This is an external-API contract, not a console pane** (ADR-0034, issue
#1523). Its consumer is an out-of-process client — today the paperclip HTTP
projection across the process boundary (`integrations/paperclip/api/`, which
names `portal/server/bridge.py` and `/api/v1/bridge*` as its serving layer).

**The browser console is deliberately not a consumer.** No asset under
`portal/static/**` calls `/api/v1/bridge*`, and that absence is the decision
rather than an unfinished wiring: the console reads the same four state families
at their own unversioned, same-origin addresses (`/api/fleet/*`,
`/api/telemetry/*`, `/api/finops/*`, `/api/ops/*`), each owned by the lane that
owns the data, and it ships as one artifact with this server. The version is
what a client that cannot move with this repository needs. A console caller here
would be a *second* read path for data the console already has, so an audit that
finds no console caller has found the contract working as decided — see
ADR-0034 before filing it as a gap.

## What already existed vs what this adds

Honesty first — most of the *data* was already served, unversioned:

| Family | Already on `master` | This bridge adds |
|--------|---------------------|------------------|
| `registry` | `portal/server/livestore.py` `RegistrySnapshot`; the roster at `GET /api/tenants/<id>/agents` (issue #348) | a direct versioned read that projects the live seeds under `ao.bridge/v1` |
| `gateway` | `gateway/proxy/config/routing.yaml`, `gateway/catalog/modules/*/module.json` (owned by the gateway lane) | a versioned read of that declarative contract + provider catalog |
| `telemetry` | `portal/server/live_feed.py` (`/api/telemetry/stream`, `/recent`, issue #345); `portal/server/finops.py` (`/api/finops/*`, issue #341); `portal/server/ops_health.py` (`/api/ops/*`, issue #342) | a versioned read that **delegates** to the live feed and **names** the finops/ops surfaces by reference — it does not re-derive a figure |
| `guardrails` | `portal/server/controls.py` `ControlCatalog` over `guardrails/policy/controls.yaml` | a versioned read of the policy controls catalog |

`portal/server/bridge.py` is a **façade**, not a second projection: every family
it serves is delegated to the lane that already owns that data. Nothing is
restated, and no runtime module is imported from the gateway.

## Endpoints

All endpoints are `GET` and require a verified console session (AuthN). The
whole `/api/v1/*` namespace ships **feature-flag-gated OFF** in
`infra/feature-flags/registry.yaml` (`surfaces.live_bridge`), and the flag is
checked **before** AuthN — an unpromoted surface is invisible, not merely
unauthorised.

| Endpoint | Payload |
|----------|---------|
| `GET /api/v1/bridge` | the manifest: contract id, one row per family (id, source, path, kind), and the stream descriptor |
| `GET /api/v1/bridge/registry` | the live `AgentProfile` seeds (id, version, owner, `modelTier`, `model`, capabilities) + `revision` |
| `GET /api/v1/bridge/gateway` | `routes`, `tierMap`, `providerChains` (verbatim from `routing.yaml`) + the provider catalog |
| `GET /api/v1/bridge/telemetry` | the live feed's own hydration payload + the named `/api/finops/overview` and `/api/ops/overview` surfaces + the ledger's presence |
| `GET /api/v1/bridge/guardrails` | the policy controls catalog + `revision` |
| `GET /api/v1/bridge/stream` | SSE: one manifest frame on connect, then a frame per family-revision change |

`?limit=<n>` bounds the telemetry hydration window (default 50, max 200).

## Authorization

The session pipeline (authN) runs before the bridge for every endpoint. For
authorization:

- `registry`, `gateway`, `guardrails` are **platform config** — readable by any
  authenticated principal, exactly like the portal-surfaces feed
  (`GET /api/portal/surfaces`).
- `telemetry` is **tenant-scoped** through the live feed's own rule: a
  super-admin sees the whole platform; anyone else sees only the tenants whose
  scope they hold and where their role grants `event:read`. A principal with no
  visible tenant is **refused** (`403 permission_denied`), never shown an empty
  feed — an empty feed would read as "no telemetry".

## The push channel is a change signal, not a data channel

`/api/v1/bridge/stream` emits a **revision** frame (`{family, revision,
contract}`) when a family's source changes; the client re-reads the family's
`GET` for the payload. This is deliberate: the telemetry family is
tenant-scoped, and a data-carrying push would have to re-implement that scoping
per subscriber. A revision signal carries no tenant data at all.

A family's `revision` is a content digest of its source:

- `registry` — the `registry/profiles` catalog + seed files;
- `gateway` — `routing.yaml` + every `catalog/modules/*/module.json`;
- `guardrails` — `guardrails/policy/controls.yaml`;
- `telemetry` — the byte size of each tailed store (a record landing moves it).

Every family read is **re-read from its source on every call** (no cached
snapshot), which is exactly what lets a registry edit become visible without a
restart — and the property the mutation proof breaks on purpose.

## Offline by construction

No sockets, no network egress: every family reads local artifacts and stores.
`make verify` runs the tests with no network.

## See also

- `portal/server/bridge.py` — the module.
- `portal/tests/test_bridge.py` — the acceptance tests (flag gate, AuthN/AuthZ,
  family-is-the-source, live refresh, push signal).
- `infra/feature-flags/registry.yaml` — the `live_bridge` flag.
- `docs/ARCHITECTURE.md` — the five-pillar architecture.
