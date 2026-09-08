/**
 * Consumer SDK transports (TypeScript) — issue #41.
 *
 * The SDK talks to the platform through small seams so every client is fully
 * testable offline against doubles and a deployment swaps in real HTTP:
 *
 * - `Transport` — one request/response round trip returning the parsed JSON
 *   body (gateway single dispatch + control-plane client);
 * - `StreamTransport` — one streaming round trip yielding parsed JSON bodies,
 *   mirroring the gateway streaming handler (issue #16: incremental
 *   `{"event": ...}` envelopes then the terminal `{status, result, record}`).
 *
 * `HttpTransport` is a dependency-free fetch-based HTTP implementation of
 * `Transport`.  The streaming adapter (an SSE relay) is the documented
 * production wiring point; offline doubles implement `StreamTransport`
 * directly.  No SDK test touches the network.
 */

import { TransportError } from "./errors.js";

export interface RequestOptions {
  /** JSON-serializable request body (POST/PUT). */
  body?: unknown;
  /** Query parameters (undefined values are dropped). */
  query?: Record<string, string | number | boolean | undefined>;
  /** The short-lived session token (attached as `Authorization: Bearer`). */
  token?: string;
  /** Extra headers. */
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

function dropUndefined(query: Record<string, string | number | boolean | undefined> | undefined) {
  if (!query) {
    return undefined;
  }
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined) {
      out[key] = String(value);
    }
  }
  return Object.keys(out).length > 0 ? out : undefined;
}

function buildUrl(baseUrl: string, path: string, query?: Record<string, string>): string {
  let url = `${baseUrl.replace(/\/+$/, "")}${path}`;
  if (query) {
    url = `${url}?${new URLSearchParams(query).toString()}`;
  }
  return url;
}

/** Dependency-free HTTP transport (`fetch`) — the default production seam. */
export class HttpTransport implements Transport {
  private readonly baseUrl: string;
  private readonly timeoutMs: number;

  constructor(baseUrl: string, opts: { timeoutMs?: number } = {}) {
    if (!baseUrl || !/^https?:\/\//.test(baseUrl)) {
      throw new Error("base_url must be an http(s) URL");
    }
    this.baseUrl = baseUrl;
    this.timeoutMs = opts.timeoutMs ?? 30_000;
  }

  async request(method: string, path: string, opts: RequestOptions = {}): Promise<Record<string, unknown>> {
    const headers: Record<string, string> = { ...(opts.headers ?? {}) };
    if (opts.token) {
      headers["Authorization"] = `Bearer ${opts.token}`;
    }
    let body: BodyInit | undefined;
    if (opts.body !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(opts.body);
    }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    let response: Response;
    try {
      response = await fetch(buildUrl(this.baseUrl, path, dropUndefined(opts.query)), {
        method,
        headers,
        body,
        signal: controller.signal,
      });
    } catch (error) {
      throw new TransportError(`transport failure: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      clearTimeout(timer);
    }
    const text = await response.text();
    let parsed: unknown;
    try {
      parsed = text.length > 0 ? JSON.parse(text) : {};
    } catch {
      throw new TransportError("response was not valid JSON");
    }
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      throw new TransportError("response body was not a JSON object");
    }
    return parsed as Record<string, unknown>;
  }
}
