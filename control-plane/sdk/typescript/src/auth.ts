/**
 * Consumer SDK auth (TypeScript) — issue #41 AC3.
 *
 * The SDK consumes SHORT-LIVED, per-tenant session tokens issued by the
 * platform (console/session) and never stores, generates or hardcodes keys.
 * A token comes from an injected provider — by default the
 * `AGENTORCH_SESSION_TOKEN` environment variable or a caller-supplied
 * callback — and every request attaches `Authorization: Bearer <token>`.
 *
 * Verification (signature, revocation, expiry) belongs to the platform edge
 * / control plane (issues #35/#37/#38).  The SDK only decodes the claims it
 * needs (tenant scoping + expiry) and fails closed when a token is absent,
 * malformed or expired.
 */

import type { SessionClaims } from "./model.js";
import { ConfigurationError, UnauthorizedError } from "./errors.js";

/** Default environment variable holding the short-lived session token. */
export const DEFAULT_TOKEN_ENV = "AGENTORCH_SESSION_TOKEN";

/** TokenProvider = a zero-argument callable returning the current token. */
export type TokenProvider = () => string | undefined;

/** Read the session token from an environment variable (undefined when unset). */
export function tokenFromEnv(envVar: string = DEFAULT_TOKEN_ENV): string | undefined {
  const value = (globalThis.process?.env as Record<string, string | undefined> | undefined)?.[envVar];
  return value && value.trim().length > 0 ? value : undefined;
}

function b64urlDecode(segment: string): string {
  const padded = segment + "=".repeat((4 - (segment.length % 4)) % 4);
  const bytes = Uint8Array.from(atob(padded.replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}

/** A parsed view of a JWT-shaped session token (claims the SDK consumes). */
export class SessionToken {
  constructor(
    public readonly tenantId: string,
    public readonly subject: string,
    public readonly subjectType: "user" | "agent" = "user",
    public readonly role?: string,
    public readonly agentId?: string,
    public readonly expiresAt?: number,
    public readonly claims: SessionClaims = { sub: "", tenantId }
  ) {}

  /** True when the token carries an `exp` in the past (short-lived). */
  get expired(): boolean {
    if (this.expiresAt === undefined) {
      return false;
    }
    return Date.now() / 1000 >= this.expiresAt;
  }

  /**
   * Parse a compact session token, failing closed on a bad shape.  Only the
   * claims the SDK needs are surfaced; the platform verifies the signature.
   * A missing mandatory `tenantId` claim is a hard error.
   */
  static parse(compactToken: string): SessionToken {
    const parts = compactToken.split(".");
    if (parts.length !== 3) {
      throw new UnauthorizedError("session token must be header.payload.signature");
    }
    let claims: SessionClaims;
    try {
      claims = JSON.parse(b64urlDecode(parts[1])) as SessionClaims;
    } catch {
      throw new UnauthorizedError("session token payload is not valid JSON");
    }
    if (typeof claims.tenantId !== "string" || claims.tenantId.length === 0) {
      throw new UnauthorizedError("session token must carry a non-empty tenantId claim");
    }
    return new SessionToken(
      claims.tenantId,
      claims.sub ?? "",
      claims.subjectType ?? "user",
      claims.role,
      claims.agentId,
      claims.exp,
      claims
    );
  }
}

/** Resolves the current session token from an env var and/or a callback. */
export class TokenSource {
  private readonly envVar: string;
  private readonly callback?: TokenProvider;

  constructor(opts: { envVar?: string; callback?: TokenProvider } = {}) {
    this.envVar = opts.envVar ?? DEFAULT_TOKEN_ENV;
    this.callback = opts.callback;
  }

  /** The current token (undefined when none is available). */
  token(): string | undefined {
    if (this.callback !== undefined) {
      const value = this.callback();
      if (value) {
        return value;
      }
    }
    return tokenFromEnv(this.envVar);
  }

  /** Require a token; throws `ConfigurationError` when none is available. */
  require(): string {
    const value = this.token();
    if (!value) {
      throw new ConfigurationError(
        `no session token available: set ${this.envVar} or supply a token callback`
      );
    }
    return value;
  }

  /** The `Authorization` header value for the current token. */
  bearer(): string {
    return `Bearer ${this.require()}`;
  }
}

/** Build an `Authorization` header value from a compact session token. */
export function bearerToken(compactToken: string): string {
  return `Bearer ${compactToken}`;
}

/** Parse a token and throw `UnauthorizedError` when it is invalid or expired. */
export function verifyNotExpired(token: string): SessionToken {
  const session = SessionToken.parse(token);
  if (session.expired) {
    throw new UnauthorizedError("session token expired");
  }
  return session;
}
