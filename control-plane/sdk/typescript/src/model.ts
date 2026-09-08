/**
 * Consumer SDK typed models (TypeScript) — issue #41.
 *
 * Field vocabulary is CONSUMED from the merged contracts and never redefined:
 * the gateway dispatch shape (issue #16 `gateway/proxy`), the control-plane
 * envelope (issue #38 `identity/cpapi`) and public-edge routes (issue #37
 * `identity/edges`), the session-token claims (issues #10/#35), and the MCP
 * tool vocabulary (issue #20 `gateway/mcp`).  Wire keys are the platform's
 * camelCase JSON keys.
 */

// --------------------------------------------------------------------------- //
// Closed dispatch-outcome vocabulary (issue #16)                               //
// --------------------------------------------------------------------------- //
export const OUTCOME_SUCCESS = "success";
export const OUTCOME_CACHE_HIT = "cache_hit";
export const OUTCOME_BLOCKED = "blocked";
export const OUTCOME_RATE_LIMITED = "rate_limited";
export const OUTCOME_REFUSED = "refused";
export const OUTCOME_CANNOT_ASSESS = "cannot_assess";
export const OUTCOME_NO_HEALTHY_ROUTE = "no_healthy_route";
export const OUTCOME_FAILED = "failed";
export const OUTCOME_DENIED = "denied";

/**
 * A dispatched task finished with an explicit non-served outcome.  `result`
 * is the typed `TaskResult` so the caller can inspect `outcome`/`error`.
 */
export class TaskNotServedError extends Error {
  constructor(
    public readonly outcome: TaskOutcome,
    public readonly result: TaskResult
  ) {
    super(`task not served (outcome=${outcome})`);
    this.name = "TaskNotServedError";
  }
}

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

export const OUTCOMES: ReadonlySet<TaskOutcome> = new Set<TaskOutcome>([
  OUTCOME_SUCCESS,
  OUTCOME_CACHE_HIT,
  OUTCOME_BLOCKED,
  OUTCOME_RATE_LIMITED,
  OUTCOME_REFUSED,
  OUTCOME_CANNOT_ASSESS,
  OUTCOME_NO_HEALTHY_ROUTE,
  OUTCOME_FAILED,
  OUTCOME_DENIED,
]);

export type ServedOutcome = "success" | "cache_hit";

export function isServedOutcome(outcome: TaskOutcome): boolean {
  return outcome === OUTCOME_SUCCESS || outcome === OUTCOME_CACHE_HIT;
}

/** Outcome -> HTTP status semantic of the gateway REST surface (issue #16). */
export const OUTCOME_STATUS: Record<TaskOutcome, number> = {
  success: 200,
  cache_hit: 200,
  blocked: 429,
  rate_limited: 429,
  refused: 422,
  cannot_assess: 422,
  no_healthy_route: 503,
  failed: 502,
  denied: 403,
};

// --------------------------------------------------------------------------- //
// Gateway task models (issue #16)                                             //
// --------------------------------------------------------------------------- //
export interface TaskRequest {
  /** The tenant (agent org) the task runs for. */
  tenantId: string;
  /** A published prompt-module task type. */
  taskType: string;
  /** Per-call render-variable map for the frozen prompt bodies. */
  input?: Record<string, unknown>;
  /** Optional difficulty 0-100 for the FinOps escalation. */
  complexity?: number;
  /** Optional token estimate for budgeting. */
  tokens?: number;
  /** Request streaming semantics. */
  stream?: boolean;
  /** Optional caller-supplied request id. */
  requestId?: string;
  /** Optional opaque metadata. */
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

/** The terminal result of one task dispatch (typed, fail closed). */
export class TaskResult {
  constructor(public readonly wire: TaskResultWire) {}

  get requestId(): string {
    return this.wire.requestId;
  }
  get tenantId(): string {
    return this.wire.tenantId;
  }
  get agentId(): string {
    return this.wire.agentId;
  }
  get taskType(): string {
    return this.wire.taskType;
  }
  get outcome(): TaskOutcome {
    return this.wire.outcome;
  }
  /** The schema-validated typed object on a served outcome; undefined otherwise. */
  get content(): unknown {
    return this.wire.content;
  }
  get provider(): string | null | undefined {
    return this.wire.provider;
  }
  get model(): string | null | undefined {
    return this.wire.model;
  }
  get record(): GatewayCallRecord | null | undefined {
    return this.wire.record;
  }

  served(): boolean {
    return isServedOutcome(this.outcome);
  }

  /** Return this when served; otherwise throw `TaskNotServedError`. */
  ensureServed(): TaskResult {
    if (!this.served()) {
      throw new TaskNotServedError(this.outcome, this);
    }
    return this;
  }

  static from(raw: Record<string, unknown>): TaskResult {
    if (!OUTCOMES.has(raw.outcome as TaskOutcome)) {
      throw new Error(`unknown task-result outcome: ${String(raw.outcome)}`);
    }
    return new TaskResult(raw as unknown as TaskResultWire);
  }
}

/** The gateway REST envelope `{status, result, record}` (issue #16). */
export interface TaskEnvelope {
  status: number;
  result: Record<string, unknown>;
  record?: GatewayCallRecord | null;
}

// --------------------------------------------------------------------------- //
// Control-plane models (usage / audit / policy; issues #37/#38)               //
// --------------------------------------------------------------------------- //
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

// --------------------------------------------------------------------------- //
// MCP models (issue #20)                                                      //
// --------------------------------------------------------------------------- //
export interface ToolDefinition {
  name: string;
  description?: string;
  inputSchema?: Record<string, unknown>;
}

export interface ToolResult {
  content: Array<{ type?: string; text?: string }>;
  isError: boolean;
}

export function toolResultText(result: ToolResult): string {
  return result.content
    .filter((item) => item.type === "text" && item.text !== undefined)
    .map((item) => item.text as string)
    .join("\n");
}

// --------------------------------------------------------------------------- //
// Session-token claims (issues #10/#35) — decoded, never verified by the SDK   //
// --------------------------------------------------------------------------- //
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
