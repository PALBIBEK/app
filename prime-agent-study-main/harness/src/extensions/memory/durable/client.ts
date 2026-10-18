/**
 * HTTP client for Memory Service Tier 1 — thin translate, no memory logic.
 */
import type { DurableMemoryConfig, DurableMemoryScope } from "./config.js";

export type SessionEventWire = {
  type: string;
  data?: Record<string, unknown>;
  time?: number;
};

export type LoadedSession = {
  id: string;
  status: string;
  repaired: boolean;
  events: Array<{
    type: string;
    seq: number;
    time: number;
    data: Record<string, unknown>;
  }>;
};

export type FetchLike = (
  input: string,
  init?: { method?: string; headers?: Record<string, string>; body?: string },
) => Promise<{
  ok: boolean;
  status: number;
  statusText: string;
  json(): Promise<unknown>;
  text(): Promise<string>;
}>;

export class DurableMemoryClient {
  constructor(
    private readonly baseUrl: string,
    private readonly fetchImpl: FetchLike = globalThis.fetch as FetchLike,
  ) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  static fromConfig(
    cfg: DurableMemoryConfig,
    fetchImpl?: FetchLike,
  ): DurableMemoryClient {
    return new DurableMemoryClient(cfg.base_url, fetchImpl);
  }

  private async request<T>(
    method: string,
    path: string,
    body?: unknown,
  ): Promise<T> {
    const res = await this.fetchImpl(`${this.baseUrl}${path}`, {
      method,
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const payload = (await res.json()) as { detail?: unknown };
        detail = JSON.stringify(payload.detail ?? payload);
      } catch {
        try {
          detail = await res.text();
        } catch {
          /* keep statusText */
        }
      }
      throw new Error(`memory service ${method} ${path} failed (${res.status}): ${detail}`);
    }
    return (await res.json()) as T;
  }

  async createSession(
    scope: DurableMemoryScope,
    sessionId?: string,
  ): Promise<{ id: string; format_version: number }> {
    return this.request("POST", "/v1/sessions", {
      scope,
      session_id: sessionId,
    });
  }

  async bufferEvents(sessionId: string, events: SessionEventWire[]): Promise<void> {
    await this.request("POST", `/v1/sessions/${sessionId}/events:buffer`, { events });
  }

  async flush(sessionId: string, fence?: number | null): Promise<{ last_seq: number }> {
    return this.request("POST", `/v1/sessions/${sessionId}/flush`, {
      fence: fence ?? null,
    });
  }

  async load(sessionId: string): Promise<LoadedSession> {
    return this.request("GET", `/v1/sessions/${sessionId}`);
  }

  async acquireLease(
    sessionId: string,
    owner: string,
  ): Promise<{ fence: number; owner: string }> {
    return this.request("POST", `/v1/sessions/${sessionId}/lease`, { owner });
  }
}
