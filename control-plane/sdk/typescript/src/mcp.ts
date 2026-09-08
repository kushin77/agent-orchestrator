/**
 * MCP client bootstrap (TypeScript) — issue #41 AC1.
 *
 * A small, typed bootstrap client for the platform's tenant-scoped MCP tool
 * gateway (issue #20): minimal JSON-RPC 2.0 over the injected transport with
 * `initialize` (protocol `2024-11-05`), `ping`, `tools/list` and `tools/call`.
 *
 * The client never asserts a tenant of its own choosing: `context.tenantId`
 * is derived from the verified session claims so a caller cannot cross
 * tenants, and `context.session` rides on every `tools/call` (the platform
 * enforces identity, allowlist, rate and audit — issue #20).
 */

import { SessionToken, TokenSource, verifyNotExpired } from "./auth.js";
import { ConfigurationError, McpError } from "./errors.js";
import type { Transport } from "./transport.js";
import { ToolDefinition, ToolResult } from "./model.js";

/** Protocol version the gateway advertises (issue #20). */
export const PROTOCOL_VERSION = "2024-11-05";
/** Default MCP endpoint path (a deployment configures its own gateway URL). */
export const DEFAULT_MCP_PATH = "/v1/mcp";

export interface McpClientOptions {
  tokenSource?: TokenSource;
  tenantId?: string;
  path?: string;
  clientName?: string;
  clientVersion?: string;
}

interface JsonRpcEnvelope {
  jsonrpc: string;
  id: number;
  result?: unknown;
  error?: { code?: number; message?: string; data?: Record<string, unknown> };
}

export class McpClient {
  private readonly transport: Transport;
  private readonly tokenSource: TokenSource;
  private readonly tenantId?: string;
  private readonly path: string;
  private readonly clientName: string;
  private readonly clientVersion: string;
  private nextId = 1;

  constructor(transport: Transport, options: McpClientOptions = {}) {
    this.transport = transport;
    this.tokenSource = options.tokenSource ?? new TokenSource();
    this.tenantId = options.tenantId;
    this.path = options.path ?? DEFAULT_MCP_PATH;
    this.clientName = options.clientName ?? "agent-orchestrator-sdk";
    this.clientVersion = options.clientVersion ?? "0.1.0";
  }

  /** Fail closed: the current session must be present and not expired. */
  private session(): SessionToken {
    const token = this.tokenSource.require();
    return verifyNotExpired(token);
  }

  private rawToken(): string {
    return this.tokenSource.require();
  }

  /** One JSON-RPC round trip (throws `McpError` on a JSON-RPC error reply). */
  private async rpc(method: string, params: Record<string, unknown>): Promise<unknown> {
    const id = this.nextId++;
    const payload: JsonRpcEnvelope = {
      jsonrpc: "2.0",
      id,
      method,
      params,
    };
    const response = (await this.transport.request("POST", this.path, {
      body: payload,
      token: this.rawToken(),
    })) as unknown as JsonRpcEnvelope;
    if (typeof response !== "object" || response === null || !("id" in response)) {
      throw new McpError(-32700, "response was not a JSON-RPC object");
    }
    if (response.error !== undefined && response.error !== null) {
      throw new McpError(
        response.error.code ?? -32603,
        response.error.message ?? "json-rpc error",
        response.error.data
      );
    }
    if (!("result" in response)) {
      throw new McpError(-32603, "json-rpc response had no result");
    }
    return response.result;
  }

  /** Negotiate the protocol and advertise client capabilities. */
  async initialize(): Promise<Record<string, unknown>> {
    const result = await this.rpc("initialize", {
      protocolVersion: PROTOCOL_VERSION,
      capabilities: { tools: {} },
      clientInfo: { name: this.clientName, version: this.clientVersion },
    });
    return result as Record<string, unknown>;
  }

  /** Return `true` when the gateway answers `ping`. */
  async ping(): Promise<boolean> {
    const result = await this.rpc("ping", {});
    return result !== undefined;
  }

  /** The declared tool catalog (platform-narrowed to the session). */
  async listTools(): Promise<ToolDefinition[]> {
    this.session(); // fail closed on an absent/lapsed session
    const result = (await this.rpc("tools/list", {
      context: { session: this.rawToken() },
    })) as { tools?: ToolDefinition[] };
    return result.tools ?? [];
  }

  /** Call one declared tool with `context.session` + `context.tenantId`. */
  async callTool(name: string, arguments_: Record<string, unknown> = {}): Promise<ToolResult> {
    const session = this.session();
    const tenantId = this.tenantId ?? session.tenantId;
    if (!tenantId) {
      throw new ConfigurationError("session token must carry a tenantId claim");
    }
    const result = await this.rpc("tools/call", {
      name,
      arguments: arguments_,
      context: { session: this.rawToken(), tenantId },
    });
    return result as ToolResult;
  }
}
