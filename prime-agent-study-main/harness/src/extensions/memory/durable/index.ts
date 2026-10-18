/**
 * Phase 2 durable memory adapter — translate harness lifecycle → Memory Service.
 * No extraction, ranking, or scope policy.
 */
import { AdapterEventBuffer } from "./buffer.js";
import {
  DurableMemoryClient,
  type FetchLike,
  type LoadedSession,
  type SessionEventWire,
} from "./client.js";
import {
  loadDurableMemoryConfig,
  type DurableMemoryConfig,
  type DurableMemoryScope,
} from "./config.js";

export { AdapterEventBuffer } from "./buffer.js";
export { DurableMemoryClient } from "./client.js";
export type { FetchLike, LoadedSession, SessionEventWire } from "./client.js";
export { loadDurableMemoryConfig } from "./config.js";
export type { DurableMemoryConfig, DurableMemoryScope } from "./config.js";

export type DurableMemoryAdapterOptions = {
  cwd?: string;
  config?: DurableMemoryConfig;
  fetchImpl?: FetchLike;
  /** Injected client (tests). */
  client?: DurableMemoryClient;
};

export class DurableMemoryAdapter {
  private readonly cfg: DurableMemoryConfig;
  private readonly client: DurableMemoryClient;
  private readonly buffer = new AdapterEventBuffer();
  private sessionId: string | null;
  private fence: number | null = null;
  private started = false;

  constructor(opts: DurableMemoryAdapterOptions = {}) {
    const cwd = opts.cwd ?? process.cwd();
    const cfg = opts.config ?? loadDurableMemoryConfig(cwd);
    if (!cfg) {
      throw new Error(
        "durable memory adapter requires .yal/memory_service.json or explicit config",
      );
    }
    this.cfg = cfg;
    this.client = opts.client ?? DurableMemoryClient.fromConfig(cfg, opts.fetchImpl);
    this.sessionId = cfg.session_id ?? null;
  }

  get pendingCount(): number {
    return this.buffer.size;
  }

  get id(): string | null {
    return this.sessionId;
  }

  /**
   * session/start — create or attach session; optional lease.
   * Awaited (not on token hot path).
   */
  async start(): Promise<string> {
    if (!this.sessionId) {
      const created = await this.client.createSession(this.cfg.scope);
      this.sessionId = created.id;
    } else {
      // Ensure session exists / is loadable (create with fixed id if missing)
      try {
        await this.client.load(this.sessionId);
      } catch {
        const created = await this.client.createSession(
          this.cfg.scope,
          this.sessionId,
        );
        this.sessionId = created.id;
      }
    }
    if (this.cfg.lease_owner) {
      const lease = await this.client.acquireLease(this.sessionId, this.cfg.lease_owner);
      this.fence = lease.fence;
    }
    this.started = true;
    return this.sessionId;
  }

  /**
   * session/event — synchronous buffer only. MUST NOT await.
   */
  pushEvent(type: string, data: Record<string, unknown> = {}, time?: number): void {
    this.buffer.push(type, data, time);
  }

  /**
   * session/flush — durability barrier at semantic checkpoints.
   */
  async flush(): Promise<{ last_seq: number; flushed: number }> {
    if (!this.started || !this.sessionId) {
      throw new Error("durable memory adapter: call start() before flush()");
    }
    const batch = this.buffer.drain();
    if (batch.length === 0) {
      return { last_seq: -1, flushed: 0 };
    }
    await this.client.bufferEvents(this.sessionId, batch);
    const { last_seq } = await this.client.flush(this.sessionId, this.fence);
    return { last_seq, flushed: batch.length };
  }

  /**
   * Resume — load repaired transcript from Memory Service (not supervisor journal).
   */
  async resume(): Promise<LoadedSession> {
    if (!this.sessionId) {
      await this.start();
    }
    const loaded = await this.client.load(this.sessionId!);
    this.started = true;
    return loaded;
  }

  /** Test/helper: snapshot pending without drain. */
  peekPending(): readonly SessionEventWire[] {
    return this.buffer.peek();
  }
}

export type DurableExtensionAPI = {
  on(
    event: "session_start" | "session_flush" | "session_before_compact",
    handler: (...args: unknown[]) => unknown,
  ): void;
};

/**
 * Register lifecycle hooks when bind is present.
 * Returns false if `.yal/memory_service.json` is absent (noop).
 */
export function registerDurableMemory(
  api: DurableExtensionAPI,
  cwd: string = process.cwd(),
  opts: Omit<DurableMemoryAdapterOptions, "cwd" | "config"> = {},
): DurableMemoryAdapter | null {
  const cfg = loadDurableMemoryConfig(cwd);
  if (!cfg) {
    return null;
  }
  const adapter = new DurableMemoryAdapter({ cwd, config: cfg, ...opts });
  api.on("session_start", async () => {
    await adapter.start();
  });
  api.on("session_flush", async () => {
    await adapter.flush();
  });
  return adapter;
}
