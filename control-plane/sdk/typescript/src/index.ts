/**
 * agent-orchestrator consumer SDK (TypeScript) — issue #41.
 *
 * Typed, transport-injected SDK for the model-gateway dispatch surface
 * (issue #16), the control-plane usage / audit / policy surface (issues
 * #37/#38) and the tenant-scoped MCP tool gateway (issue #20).
 *
 * - **Offline by construction.**  Every client takes an injected `Transport`;
 *   production wires `HttpTransport` (fetch) or an SSE streaming adapter.
 * - **Auth = short-lived per-tenant session tokens** from
 *   `AGENTORCH_SESSION_TOKEN` or an injected callback.  Never a hardcoded key.
 * - **Typed, fail closed.**  Gateway outcomes are a closed set; a non-served
 *   outcome never carries fabricated typed content.
 *
 * @module
 *
 * ---knowledge---
 * module_id: control-plane.sdk.typescript.src.index
 * system: control-plane
 * app: sdk
 * solution_class: class
 * patterns: [public-surface, re-export-only, offline-by-construction]
 * derives_from: null
 * owner_sme: platform-sme
 * tier: L0
 * interfaces: [SDK_VERSION, re-exports of model, errors, auth, transport, envelope, gateway, controlPlane, mcp]
 * invariants: "the package's public surface is exactly these re-exports, so a consumer imports the SDK and never a deep module path"
 * gotchas: "every client takes an injected Transport, so the module is offline by construction"
 * related: ["#41"]
 * do_not_duplicate: null
 * ---knowledge---
 */

export * from "./model.js";
export * from "./errors.js";
export * from "./auth.js";
export * from "./transport.js";
export * from "./envelope.js";
export * from "./gateway.js";
export * from "./controlPlane.js";
export * from "./mcp.js";

export const SDK_VERSION = "0.1.0";
