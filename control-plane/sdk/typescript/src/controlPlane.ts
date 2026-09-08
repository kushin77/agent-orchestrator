/**
 * Control-plane client (TypeScript) — usage, audit export, policy (issue #41
 * AC1).  Typed client over the control-plane REST surface (issue #38) as
 * published through the public edge routes (issue #37): tenant usage
 * (`GET /v1/tenants/{tenantId}/usage` + the public `me` route), audit export
 * (`GET /v1/tenants/me/audit/export` / `GET /v1/audit`) and declared policy
 * bindings (`GET /v1/policies` / `GET /v1/policies/{policyId}`).
 *
 * Responses ride the standardized control-plane envelope; the client unwraps
 * it and throws the matching `ApiError` on a non-OK envelope.  Requests carry
 * a short-lived per-tenant session token (env or callback) — never a key.
 */

import { SessionToken, TokenSource, verifyNotExpired } from "./auth.js";
import { items, requireOk } from "./envelope.js";
import { ConfigurationError } from "./errors.js";
import type { Transport } from "./transport.js";
import type { AuditRecord, PolicyBinding, UsageReport } from "./model.js";

export class ControlPlaneClient {
  private readonly transport: Transport;
  private readonly tokenSource: TokenSource;
  private readonly tenantId?: string;

  constructor(transport: Transport, opts: { tokenSource?: TokenSource; tenantId?: string } = {}) {
    this.transport = transport;
    this.tokenSource = opts.tokenSource ?? new TokenSource();
    this.tenantId = opts.tenantId;
  }

  /** Require a token, failing closed on an absent/lapsed token. */
  private token(): string {
    const value = this.tokenSource.require();
    verifyNotExpired(value);
    return value;
  }

  /** Resolve the effective tenant (explicit or session claim). */
  private tenant(): string {
    const session = SessionToken.parse(this.token());
    const tenantId = this.tenantId ?? session.tenantId;
    if (!tenantId) {
      throw new ConfigurationError(
        "no tenant context: pass tenant_id or use a session token with a tenantId claim"
      );
    }
    return tenantId;
  }

  private async call(method: string, path: string, opts: { body?: unknown; query?: Record<string, string | number | undefined> } = {}): Promise<unknown> {
    return requireOk(await this.transport.request(method, path, { body: opts.body, query: opts.query, token: this.token() }));
  }

  /** Tenant usage summary for `tenantId` (or the session tenant). */
  async getUsage(tenantId?: string): Promise<UsageReport> {
    const resolved = this.tenant();
    if (tenantId !== undefined && tenantId !== resolved) {
      // A caller may only read its own tenant (no cross-tenant fallback).
      throw new ConfigurationError(
        `tenant_id ${tenantId} differs from the session tenant ${resolved}`
      );
    }
    const data = await requireOk(
      await this.transport.request("GET", `/v1/tenants/${resolved}/usage`, { token: this.token() })
    );
    return data as UsageReport;
  }

  /** Usage for the caller's tenant via the public `me` route (issue #37). */
  async myUsage(): Promise<UsageReport> {
    const data = await requireOk(
      await this.transport.request("GET", "/v1/tenants/me/usage", { token: this.token() })
    );
    return data as UsageReport;
  }

  /** The tenant's audit export feed (public edge route, issue #37). */
  async exportAudit(options: { limit?: number; action?: string } = {}): Promise<AuditRecord[]> {
    const query: Record<string, string | number | undefined> = {};
    if (options.limit !== undefined) {
      query.limit = options.limit;
    }
    if (options.action) {
      query.action = options.action;
    }
    const data = await requireOk(
      await this.transport.request("GET", "/v1/tenants/me/audit/export", { query, token: this.token() })
    );
    return items(data) as AuditRecord[];
  }

  /** Query the control-plane audit ledger (`GET /v1/audit`, issue #38). */
  async queryAudit(options: { limit?: number; action?: string; actor?: string } = {}): Promise<AuditRecord[]> {
    const query: Record<string, string | number | undefined> = {};
    if (options.limit !== undefined) {
      query.limit = options.limit;
    }
    if (options.action) {
      query.action = options.action;
    }
    if (options.actor) {
      query.actor = options.actor;
    }
    const data = await this.call("GET", "/v1/audit", { query });
    return items(data) as AuditRecord[];
  }

  /** Declared policy bindings (`GET /v1/policies`, issue #38). */
  async listPolicies(): Promise<PolicyBinding[]> {
    const data = await this.call("GET", "/v1/policies");
    return items(data) as PolicyBinding[];
  }

  /** One declared policy binding (`GET /v1/policies/{policyId}`). */
  async getPolicy(policyId: string): Promise<PolicyBinding> {
    const data = await this.call("GET", `/v1/policies/${encodeURIComponent(policyId)}`);
    return data as PolicyBinding;
  }
}
