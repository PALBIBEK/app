/**
 * Sync write-behind buffer — hot path must not await I/O.
 */
import type { SessionEventWire } from "./client.js";

export type BufferedEvent = SessionEventWire & { buffered_at: number };

export class AdapterEventBuffer {
  private pending: BufferedEvent[] = [];

  get size(): number {
    return this.pending.length;
  }

  /** Synchronous — no I/O. */
  push(type: string, data: Record<string, unknown> = {}, time?: number): void {
    this.pending.push({
      type,
      data,
      time: time ?? Date.now(),
      buffered_at: Date.now(),
    });
  }

  peek(): readonly BufferedEvent[] {
    return this.pending;
  }

  drain(): SessionEventWire[] {
    const batch = this.pending.map(({ type, data, time }) => ({ type, data, time }));
    this.pending = [];
    return batch;
  }

  clear(): void {
    this.pending = [];
  }
}
