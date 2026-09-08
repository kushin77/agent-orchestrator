/**
 * Consumer SDK type declarations (TypeScript) — issue #41.
 *
 * Hand-maintained declaration mirror of `src/**` for consumers that import
 * the package's types without compiling the sources (regenerated from `src`
 * by `npm run build` -> `tsc -d` when the toolchain is available).  The
 * vocabulary matches the Python SDK and the merged platform contracts.
 */

export declare const SDK_VERSION = "0.1.0";

// --------------------------------------------------------------------------- //
// model: closed outcome vocabulary + typed wire models                         //
// --------------------------------------------------------------------------- //
export declare const OUTCOME_SUCCESS: "success";
export declare const OUTCOME_CACHE_HIT: "cache_hit";
export declare const OUTCOME_BLOCKED: "blocked";
export declare const OUTCOME_RATE_LIMITED: "rate_limited";
export declare const OUTCOME_REFUSED: "refused";
export declare const OUTCOME_CANNOT_ASSESS: "cannot_assess";
export declare const OUTCOME_NO_HEALTHY_ROUTE: "no_healthy_route";
export declare const OUTCOME_FAILED: "failed";
export declare const OUTCOME_DENIED: "denied";

export type TaskOutcome =
  | "success"
  | "cache_hit"
  | "blocked"
  | "rate_limited"
  | "refused"
  | "cannot_assess"
  | "no_healthy_route"
  | "failed"
  | "denied";

export declare const OUTCOMES: ReadonlySet<TaskOutcome>;
export declare function isServedOutcome(outcome: TaskOutcome): boolean;
export declare const OUTCOME_STATUS: Record<TaskOutcome, number>;

export interface TaskRequest {
  tenantId: string;
  taskType: string;
  input?: Record<string, unknown>;
  complexity?: number;
  tokens?: number;
  stream?: boolean;
  requestId?: string;
  metadata?: Record<string, unknown>;
}

export interface DispatchEvent {
  requestId: string;
  stage: string;
  data: Record<string, unknown>;
}

export interface GatewayCallRecord {
  requestId: string;
  ts: string;
  tenantId: string;
  agentId: string;
  taskType: string;
  outcome: TaskOutcome;
  capability?: string | null;
  taskClass?: string | null;
  tier?: string | null;
  provider?: string | null;
  model?: string | null;
  inputTokens?: number;
  outputTokens?: number;
  latencyMs?: number;
  estimatedCostUsd?: number;
  budgetAction?: string;
  attempts?: number;
  error?: string | null;
}

export interface TaskResultWire {
  requestId: string;
  tenantId: string;
  agentId: string;
  taskType: string;
  outcome: TaskOutcome;
  content?: unknown;
  provider?: string | null;
  model?: string | null;
  tier?: string | null;
  inputTokens?: number;
  outputTokens?: number;
  latencyMs?: number;
  error?: string | null;
  record?: GatewayCallRecord | null;
}

export declare class TaskResult {
  constructor(wire: TaskResultWire);
  readonly wire: TaskResultWire;
  readonly requestId: string;
  readonly tenantId: string;
  readonly agentId: string;
  readonly taskType: string;
  readonly outcome: TaskOutcome;
  readonly content: unknown;
  readonly provider: string | null | undefined;
  readonly model: string | null | undefined;
  readonly record: GatewayCallRecord | null | undefined;
  served(): boolean;
  ensureServed(): TaskResult;
  static from(raw: Record<string, unknown>): TaskResult;
}

export declare class TaskNotServedError extends Error {
  readonly outcome: TaskOutcome;
  readonly result: TaskResult;
  constructor(outcome: TaskOutcome, result: TaskResult);
}

export interface TaskEnvelope {
  status: number;
  result: Record<string, unknown>;
  record?: GatewayCallRecord | null;
}

export interface UsageBudget {
  limitUsd?: number | null;
  spentUsd?: number;
  action?: "observe" | "enforce";
  warnAtPct?: number | null;
  status?: "ok" | "warn" | "blocked";
}

export interface UsageReport {
  tenantId: string;
  period: string;
  calls?: number;
  inputTokens?: number;
  outputTokens?: number;
  estimatedCostUsd?: number;
  budget?: UsageBudget | null;
}

export interface AuditRecord {
  seq: number;
  ts: string;
  actor: string;
  action: string;
  resource: string;
  tenantId?: string;
  detail?: Record<string, unknown>;
}

export interface PolicyBinding {
  policyId: string;
  name?: string;
  bundle?: string | null;
  version?: string | null;
  tenantId?: string;
  enabled?: boolean;
  controls?: string[];
}

export interface ToolDefinition {
  name: string;
  description?: string;
  inputSchema?: Record<string, unknown>;
}

export interface ToolResult {
  content: Array<{ type?: string; text?: string }>;
  isError: boolean;
}

export declare function toolResultText(result: ToolResult): string;

export interface SessionClaims {
  iss?: string;
  sub: string;
  aud?: string | string[];
  iat?: number;
  exp?: number;
  jti?: string;
  tenantId: string;
  subjectType?: "user" | "agent";
  role?: string;
  agentId?: string;
  purpose?: string;
  allowedTools?: string[];
}

// --------------------------------------------------------------------------- //
// errors                                                                      //
// --------------------------------------------------------------------------- //
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

export declare class SdkError extends Error {}
export declare class ConfigurationError extends SdkError {}
export declare class TransportError extends SdkError {}
export declare class ApiError extends SdkError {
  constructor(
    status: number,
    code: ErrorCode,
    message?: string,
    details?: Record<string, unknown> | null
  );
  readonly status: number;
  readonly code: ErrorCode;
  readonly details?: Record<string, unknown> | null;
}
export declare class UnauthorizedError extends ApiError {
  constructor(message?: string, details?: Record<string, unknown> | null);
}
export declare class ScopeDeniedError extends ApiError {
  constructor(message?: string, details?: Record<string, unknown> | null);
}
export declare class PermissionDeniedError extends ApiError {
  constructor(message?: string, details?: Record<string, unknown> | null);
}
export declare class McpError extends SdkError {
  constructor(code: number, message?: string, data?: Record<string, unknown>);
  readonly code: number;
  readonly data?: Record<string, unknown>;
}

// --------------------------------------------------------------------------- //
// auth                                                                        //
// --------------------------------------------------------------------------- //
export declare const DEFAULT_TOKEN_ENV = "AGENTORCH_SESSION_TOKEN";
export type TokenProvider = () => string | undefined;
export declare function tokenFromEnv(envVar?: string): string | undefined;

export declare class SessionToken {
  constructor(
    tenantId: string,
    subject: string,
    subjectType?: "user" | "agent",
    role?: string,
    agentId?: string,
    expiresAt?: number,
    claims?: SessionClaims
  );
  readonly tenantId: string;
  readonly subject: string;
  readonly subjectType: "user" | "agent";
  readonly role?: string;
  readonly agentId?: string;
  readonly expiresAt?: number;
  readonly claims: SessionClaims;
  readonly expired: boolean;
  static parse(compactToken: string): SessionToken;
}

export declare class TokenSource {
  constructor(opts?: { envVar?: string; callback?: TokenProvider });
  token(): string | undefined;
  require(): string;
  bearer(): string;
}

export declare function bearerToken(compactToken: string): string;
export declare function verifyNotExpired(token: string): SessionToken;

// --------------------------------------------------------------------------- //
// transport + envelope                                                        //
// --------------------------------------------------------------------------- //
export interface RequestOptions {
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined>;
  token?: string;
  headers?: Record<string, string>;
}

export interface Transport {
  request(method: string, path: string, opts?: RequestOptions): Promise<Record<string, unknown>>;
}

export interface StreamTransport {
  requestStream(
    method: string,
    path: string,
    opts?: RequestOptions
  ): AsyncIterable<Record<string, unknown>>;
}

export declare class HttpTransport implements Transport {
  constructor(baseUrl: string, opts?: { timeoutMs?: number });
  request(method: string, path: string, opts?: RequestOptions): Promise<Record<string, unknown>>;
}

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
export declare function errorFromEnvelope(envelope: ControlPlaneEnvelope): ApiError;
export declare function requireOk(envelope: unknown): unknown;
export declare function items(data: unknown): unknown[];

// --------------------------------------------------------------------------- //
// clients                                                                     //
// --------------------------------------------------------------------------- //
export interface DispatchOptions {
  input?: Record<string, unknown>;
  complexity?: number;
  tokens?: number;
  requestId?: string;
  metadata?: Record<string, unknown>;
}

export type GatewayStreamChunk = DispatchEvent | TaskResult;

export declare class GatewayClient {
  constructor(transport: Transport, opts?: { tokenSource?: TokenSource; tenantId?: string });
  dispatch(agentId: string, taskType: string, options?: DispatchOptions): Promise<TaskResult>;
  run(agentId: string, taskType: string, options?: DispatchOptions): Promise<TaskResult>;
  stream(agentId: string, taskType: string, options?: DispatchOptions): AsyncGenerator<GatewayStreamChunk>;
}

export declare class ControlPlaneClient {
  constructor(transport: Transport, opts?: { tokenSource?: TokenSource; tenantId?: string });
  getUsage(tenantId?: string): Promise<UsageReport>;
  myUsage(): Promise<UsageReport>;
  exportAudit(options?: { limit?: number; action?: string }): Promise<AuditRecord[]>;
  queryAudit(options?: { limit?: number; action?: string; actor?: string }): Promise<AuditRecord[]>;
  listPolicies(): Promise<PolicyBinding[]>;
  getPolicy(policyId: string): Promise<PolicyBinding>;
}

export declare const PROTOCOL_VERSION = "2024-11-05";
export declare const DEFAULT_MCP_PATH = "/v1/mcp";

export interface McpClientOptions {
  tokenSource?: TokenSource;
  tenantId?: string;
  path?: string;
  clientName?: string;
  clientVersion?: string;
}

export declare class McpClient {
  constructor(transport: Transport, options?: McpClientOptions);
  initialize(): Promise<Record<string, unknown>>;
  ping(): Promise<boolean>;
  listTools(): Promise<ToolDefinition[]>;
  callTool(name: string, arguments_?: Record<string, unknown>): Promise<ToolResult>;
}
