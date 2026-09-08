# Control-plane clients (issue #38 AC4 — OpenAPI spec + generated clients)

The wire contract lives in [`../openapi.yaml`](../openapi.yaml) (OpenAPI 3.0).
Two ways to consume it:

## 1. Hand-written typed client (this package)

[`control_plane_client.py`](control_plane_client.py) — `ControlPlaneClient` —
is a small, dependency-free typed client that mirrors the spec exactly: every
endpoint in `openapi.yaml` has one method, responses are unwrapped from the
standard envelope, and failures raise `cpapi.ApiError` carrying the spec's
machine `code`. It is **transport-agnostic**: you inject anything satisfying
the `Transport` protocol (one `request(method, path, body=, query=, token=)`
round trip returning the envelope).

- `cpapi.transport.InProcessTransport(control_plane)` runs the client against
  a `ControlPlane` **in-process** — the offline test harness (see
  `identity/cpapi/tests/test_client.py`).
- In a deployment, inject an HTTP transport (any `requests`/`aiohttp`/vendor
  adapter) pointed at the mounted control-plane service. The client sends the
  session token as the bearer credential; the service authenticates and
  authorizes.

## 2. Generated clients from the spec

`openapi.yaml` is a standard OpenAPI 3.0 document, so richer SDKs can be
**generated at build time** by the usual toolchains (openapi-generator,
swagger-codegen, kiota, ...) targeting any language. The hand-written client
above is intentionally kept as the canonical, dependency-free reference
implementation of the same contract so that offline behavior is never coupled
to a generator or a network call.

## Conventions

- Envelope: every response is `{ok, status, requestId, data, error}`;
  `error.code` is a stable machine string (400 `validation_error`, 401
  `invalid_token`/`session_revoked`, 403 `scope_denied`/`permission_denied`,
  404 `unknown_*`, 409 `already_registered`, 422 `refused`, 503 `unavailable`).
- Destructive actions (agent retire, tenant pause) return `202` with
  `data.status == "approval_required"` until an approver approves; after
  approval the same call executes.
- Domain events are consumed through the outbox contract: `poll` → `ack` /
  `fail` (redelivery + dead-letter are bus-side; see the cpapi README).
