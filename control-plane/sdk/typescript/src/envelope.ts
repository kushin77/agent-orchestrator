/**
 * Control-plane envelope handling (TypeScript) — issue #38, consumed.
 *
 * The control-plane REST surface answers with the standardized envelope
 * `{ok, status, requestId, data, error}` where `error` is `{code, message,
 * details}`.  `requireOk` unwraps it into `data` or throws the matching
 * `ApiError` — fail closed, never a silent pass on a non-OK envelope.  The
 * gateway task envelope (`{status, result, record}`, issue #16) is handled by
 * the gateway client.
 */

import { ApiError, PermissionDeniedError, ScopeDeniedError, UnauthorizedError } from "./errors.js";

export interface ErrorEnvelope {
  code?: string;
  message?: string;
  details?: Record<string, unknown> | null;
}

export interface ControlPlaneEnvelope {
  ok: boolean;
  status: number;
  requestId: string;
  data?: unknown;
  error?: ErrorEnvelope | null;
}

export function errorFromEnvelope(envelope: ControlPlaneEnvelope): ApiError {
  const status = envelope.status ?? 500;
  const rawError = envelope.error ?? {};
  const code = rawError.code ?? "unknown_error";
  const message = rawError.message ?? "";
  const details = rawError.details ?? null;
  if (status === 401 || code === "unauthenticated") {
    return new UnauthorizedError(message, details);
  }
  if (status === 403) {
    if (code === "scope_denied") {
      return new ScopeDeniedError(message, details);
    }
    if (code === "permission_denied") {
      return new PermissionDeniedError(message, details);
    }
  }
  return new ApiError(status, code, message, details);
}

/** Unwrap a control-plane envelope: return `data` or throw `ApiError`. */
export function requireOk(envelope: unknown): unknown {
  if (typeof envelope !== "object" || envelope === null) {
    throw new ApiError(500, "malformed_response", "response was not an envelope object");
  }
  const typed = envelope as ControlPlaneEnvelope;
  if (!typed.ok) {
    throw errorFromEnvelope(typed);
  }
  return typed.data;
}

/** Return the `items` list of a list-style payload (`{"items": [...]}`). */
export function items(data: unknown): unknown[] {
  if (typeof data === "object" && data !== null) {
    const listed = (data as { items?: unknown }).items;
    if (Array.isArray(listed)) {
      return listed;
    }
  }
  if (Array.isArray(data)) {
    return data;
  }
  return [];
}
