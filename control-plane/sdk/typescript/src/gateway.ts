/**
 * Gateway client (TypeScript) — typed tasks + streaming (issue #41 AC1).
 *
 * Wraps the model-gateway dispatch surface `POST /v1/agents/{agentId}/tasks`
 * (issue #16) behind typed, authenticated calls.  `dispatch` returns a typed
 * `TaskResult` whose `content` is the schema-validated typed object on a
 * served outcome and whose `outcome` is one of the closed set otherwise —
 * the SDK never fabricates content for a non-served outcome.  `stream` yields
 * the incremental dispatch events then the terminal `TaskResult`.
 */

import { TokenSource, verifyNotExpired } from "./auth.js";
import { ApiError, ConfigurationError } from "./errors.js";
import { errorFromEnvelope } from "./envelope.js";
import type { StreamTransport, Transport } from "./transport.js";
import { DispatchEvent, TaskRequest, TaskResult, TaskResultWire } from "./model.js";

/** Options for one task dispatch (the `POST .../tasks` body minus tenant). */
export interface DispatchOptions {
  input?: Record<string, unknown>;
  complexity?: number;
  tokens?: number;
  requestId?: string;
  metadata?: Record<string, unknown>;
}

export type GatewayStreamChunk = DispatchEvent | TaskResult;

export class GatewayClient {
  private readonly transport: Transport;
  private readonly tokenSource: TokenSource;
  private readonly tenantId?: string;

  constructor(transport: Transport, opts: { tokenSource?: TokenSource; tenantId?: string } = {}) {
    this.transport = transport;
    this.tokenSource = opts.tokenSource ?? new TokenSource();
    this.tenantId = opts.tenantId;
  }

  /** Resolve the current token + effective tenant (fail closed). */
  private tokenAndTenant(): { token: string; tenantId: string } {
    const token = this.tokenSource.require();
    const session = verifyNotExpired(token);
    const tenantId = this.tenantId ?? session.tenantId;
    if (!tenantId) {
      throw new ConfigurationError(
        "no tenant context: pass tenant_id or use a session token with a tenantId claim"
      );
    }
    return { token, tenantId };
  }

  /** Submit one task and return the typed `TaskResult`. */
  async dispatch(agentId: string, taskType: string, options: DispatchOptions = {}): Promise<TaskResult> {
    const { token, tenantId } = this.tokenAndTenant();
    const request: TaskRequest = {
      tenantId,
      taskType,
      input: options.input ?? {},
      complexity: options.complexity,
      tokens: options.tokens,
      stream: false,
      requestId: options.requestId,
      metadata: options.metadata,
    };
    const response = await this.transport.request("POST", `/v1/agents/${agentId}/tasks`, {
      body: request,
      token,
    });
    return parseGatewayResponse(response);
  }

  /** Dispatch and throw `TaskNotServedError` on a non-served outcome. */
  async run(agentId: string, taskType: string, options: DispatchOptions = {}): Promise<TaskResult> {
    return (await this.dispatch(agentId, taskType, options)).ensureServed();
  }

  /**
   * Stream one dispatch: yields incremental events, then the terminal result.
   * The transport must implement `StreamTransport` (SSE relay in production;
   * the offline doubles speak the same chunk contract).
   */
  async *stream(agentId: string, taskType: string, options: DispatchOptions = {}): AsyncGenerator<GatewayStreamChunk> {
    const transport = this.transport as unknown;
    const requestStream = (transport as StreamTransport).requestStream;
    if (typeof requestStream !== "function") {
      throw new ConfigurationError(
        "transport does not support streaming; provide a StreamTransport"
      );
    }
    const { token, tenantId } = this.tokenAndTenant();
    const request: TaskRequest = {
      tenantId,
      taskType,
      input: options.input ?? {},
      complexity: options.complexity,
      tokens: options.tokens,
      stream: true,
      requestId: options.requestId,
      metadata: options.metadata,
    };
    for await (const chunk of requestStream.call(transport, "POST", `/v1/agents/${agentId}/tasks`, {
      body: request,
      token,
    })) {
      if ("event" in chunk) {
        yield chunk.event as unknown as DispatchEvent;
      } else {
        yield parseGatewayResponse(chunk);
      }
    }
  }
}

/** Parse the issue #16 gateway envelope into a typed `TaskResult`. */
function parseGatewayResponse(response: Record<string, unknown>): TaskResult {
  if (typeof response !== "object" || response === null || !("result" in response)) {
    const envelope = response as unknown as {
      status?: number;
      error?: { code?: string; message?: string };
    };
    throw errorFromEnvelope({
      ok: false,
      status: envelope.status ?? 502,
      requestId: "",
      error: envelope.error ?? null,
    });
  }
  const raw = response.result as unknown as TaskResultWire;
  if (!raw.requestId && !raw.tenantId) {
    throw new ApiError(500, "malformed_response", "gateway envelope had no result payload");
  }
  return TaskResult.from(response.result as Record<string, unknown>);
}
