/**
 * Consumer SDK error taxonomy (TypeScript) — issue #41.
 *
 * A small closed family of errors mirroring the Python SDK.  `ApiError`
 * carries the platform's stable machine `code` plus the HTTP `status`; error
 * codes are the envelope/error codes of the merged control-plane REST
 * contract (issue #38) and the gateway outcome/status semantics (issue #16).
 *
 * ---knowledge---
 * module_id: control-plane.sdk.typescript.src.errors
 * system: control-plane
 * app: sdk
 * solution_class: class
 * patterns: [closed-error-taxonomy, machine-code-not-string-matching]
 * derives_from: control-plane/sdk/python/aosdk/errors.py
 * owner_sme: platform-sme
 * tier: L0
 * interfaces: [SdkError, ConfigurationError, TransportError, ApiError, UnauthorizedError, ScopeDeniedError, PermissionDeniedError, McpError, ErrorCode]
 * invariants: "ApiError carries the platform's stable machine code plus the HTTP status so a caller can map failures without string-matching"
 * gotchas: "the family mirrors the Python SDK's error taxonomy so both SDKs read the same envelope and gateway codes"
 * related: ["#41"]
 * do_not_duplicate: null
 * ---knowledge---
 */

/** The platform's stable machine error code (envelope `error.code`). */
export type ErrorCode =
  | "unauthenticated"
  | "scope_denied"
  | "permission_denied"
  | "not_found"
  | "unknown_route"
  | "method_not_allowed"
  | "unknown_error"
  | "malformed_response"
  | string;

export class SdkError extends Error {}

export class ConfigurationError extends SdkError {}

export class TransportError extends SdkError {}

/** A non-OK platform response, carrying the envelope error fields. */
export class ApiError extends SdkError {
  constructor(
    public readonly status: number,
    public readonly code: ErrorCode,
    message = "",
    public readonly details?: Record<string, unknown> | null
  ) {
    super(`${status} ${code}: ${message}`.trim());
    this.name = "ApiError";
  }
}

export class UnauthorizedError extends ApiError {
  constructor(message = "unauthenticated", details?: Record<string, unknown> | null) {
    super(401, "unauthenticated", message, details);
    this.name = "UnauthorizedError";
  }
}

export class ScopeDeniedError extends ApiError {
  constructor(message = "scope denied", details?: Record<string, unknown> | null) {
    super(403, "scope_denied", message, details);
    this.name = "ScopeDeniedError";
  }
}

export class PermissionDeniedError extends ApiError {
  constructor(message = "permission denied", details?: Record<string, unknown> | null) {
    super(403, "permission_denied", message, details);
    this.name = "PermissionDeniedError";
  }
}

/** A JSON-RPC error reply from the MCP surface (issue #20). */
export class McpError extends SdkError {
  constructor(
    public readonly code: number,
    message = "",
    public readonly data?: Record<string, unknown>
  ) {
    super(`mcp error ${code}: ${message}`.trim());
    this.name = "McpError";
  }
}
