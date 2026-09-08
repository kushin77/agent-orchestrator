# Consumer SDK (TypeScript) — `@kushin77/agent-orchestrator-sdk`

TypeScript twin of the Python SDK (issue `kushin77/agent-orchestrator#41`,
parent EPIC-00 #4). Part of the `control-plane/sdk` subtree. Contract overview
+ quickstart: [`../README.md`](../README.md).

Ships **source-complete** TypeScript plus a hand-maintained declaration bundle
(`types/index.d.ts`); **no dependencies and no `node_modules` are committed**,
and the package is designed to run with zero third-party runtime deps (the
`HttpTransport` uses the platform `fetch`).  The API mirrors the Python SDK
exactly (same vocabulary, same envelopes, same fail-closed semantics).

## What it provides (acceptance criterion 1)

- **Gateway client** — `GatewayClient.dispatch` / `.run` (typed tasks) and
  `.stream` (incremental `DispatchEvent`s then the terminal `TaskResult`)
  over `POST /v1/agents/{agentId}/tasks` (issue #16 vocabulary).
- **Control-plane client** — `ControlPlaneClient` usage / audit export /
  policy over the issue #37 public edge + #38 control-plane envelope.
- **MCP client bootstrap** — `McpClient` `initialize` / `ping` /
  `listTools` / `callTool` (JSON-RPC 2.0, `context.session` + derived
  `context.tenantId`; issue #20 vocabulary).
- **Auth** — `TokenSource` reading a short-lived per-tenant session token
  from `AGENTORCH_SESSION_TOKEN` or an injected callback; never a hardcoded
  key (acceptance criterion 3).

## Layout

```text
typescript/
├── README.md          # this file (offline limitations documented below)
├── package.json       # metadata only; no runtime deps, no node_modules
├── tsconfig.json      # strict TS build (typecheck/build when tsc is present)
├── src/
│   ├── index.ts       # public surface (barrel)
│   ├── model.ts       # typed models + closed outcome/claim vocabularies
│   ├── errors.ts      # error taxonomy (ApiError, McpError, ...)
│   ├── auth.ts        # short-lived session-token providers (env/callback)
│   ├── envelope.ts    # control-plane envelope unwrap
│   ├── transport.ts   # Transport/StreamTransport seams + HttpTransport
│   ├── gateway.ts     # GatewayClient (typed dispatch + streaming)
│   ├── controlPlane.ts# ControlPlaneClient (usage, audit export, policy)
│   └── mcp.ts         # McpClient (JSON-RPC bootstrap)
└── types/
    └── index.d.ts     # hand-maintained declaration mirror of the public API
```

## Quickstart (mirrors the Python SDK)

```ts
import {
  HttpTransport, TokenSource,
  GatewayClient, ControlPlaneClient, McpClient,
} from "@kushin77/agent-orchestrator-sdk";

const transport = new HttpTransport("https://edge.example.com");
const source = new TokenSource(); // AGENTORCH_SESSION_TOKEN or { callback }

// 1) typed gateway dispatch (issue #16)
const gateway = new GatewayClient(transport, { tokenSource: source });
const review = await gateway.run("coder-agent", "code-review-verdict", { input: { diff: "..." } });
console.log(review.outcome, review.content);

// 2) streaming dispatch
for await (const chunk of gateway.stream("coder-agent", "code-review-verdict", { input: { diff: "..." } })) {
  console.log(chunk);
}

// 3) control-plane: usage / audit export / policy (issues #37/#38)
const control = new ControlPlaneClient(transport, { tokenSource: source });
const usage = await control.myUsage();          // GET /v1/tenants/me/usage
const audit = await control.exportAudit();      // GET /v1/tenants/me/audit/export
const policies = await control.listPolicies();  // GET /v1/policies

// 4) MCP bootstrap (issue #20)
const mcp = new McpClient(transport, { tokenSource: source });
await mcp.initialize();
const tools = await mcp.listTools();
const reply = await mcp.callTool("kb.summary", { moduleId: "core" });
```

## Offline limitation (documented)

The Python SDK is the **offline-verified gate** for this issue: it is fully
implemented and exercised end-to-end with `pytest` against offline platform
doubles under `control-plane/sdk/python/tests/` (no network, stdlib only).

This TypeScript SDK is delivered **source-complete with hand-maintained type
declarations**, but it is **not executed in this repository's offline
verification**: compiling/type-checking it requires `tsc` (via `npm install`,
which this environment does not assume and which would create a
`node_modules`).  Zero runtime dependencies are required — `HttpTransport`
uses the platform `fetch` — so once a consumer has a TypeScript toolchain it
type-checks with:

```bash
npm install --dev        # typescript only (no runtime deps)
npm run typecheck        # tsc --noEmit
npm run build            # tsc -> dist/ + regenerated types/
```

The API surface, vocabulary and fail-closed semantics are line-for-line the
Python SDK's (verified by the Python suite), so the TS side is correct by
construction against the same merged contracts (#16/#20/#37/#38).

## Auth (acceptance criterion 3)

Short-lived, per-tenant session tokens come from `TokenSource` (the
`AGENTORCH_SESSION_TOKEN` env var or an injected `callback` that may refresh
the token on every call).  Tokens are attached as `Authorization: Bearer` on
every request; the SDK decodes only the claims it needs and never stores a
key.  Verification belongs to the platform edge / control plane.

## Provenance

See [`../README.md`](../README.md) for the full provenance table.  The TS SDK
follows the same harvested conventions as the Python SDK (`saas-rbac` SDK
conventions + `identity/cpapi/clients` client shape, issue #38).
