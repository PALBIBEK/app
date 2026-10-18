/**
 * Node tests for durable memory adapter (Phase 2).
 * Run: npm run build && node --test dist/extensions/memory/durable/adapter.test.js
 * (compiled from this file next to adapter — see scripts/test-durable-memory.mjs)
 */
import assert from "node:assert/strict";
import { mkdirSync, writeFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import test from "node:test";
import {
  AdapterEventBuffer,
  DurableMemoryAdapter,
  DurableMemoryClient,
  loadDurableMemoryConfig,
  registerDurableMemory,
} from "./index.js";

function mockFetch(handlers: Record<string, (body?: unknown) => unknown>) {
  const calls: Array<{ method: string; url: string; body?: unknown }> = [];
  const fetchImpl = async (
    input: string,
    init?: { method?: string; headers?: Record<string, string>; body?: string },
  ) => {
    const method = (init?.method ?? "GET").toUpperCase();
    const body = init?.body ? JSON.parse(init.body) : undefined;
    calls.push({ method, url: input, body });
    const key = `${method} ${new URL(input).pathname}`;
    const handler = handlers[key];
    if (!handler) {
      return {
        ok: false,
        status: 404,
        statusText: `no handler for ${key}`,
        async json() {
          return { detail: `no handler for ${key}` };
        },
        async text() {
          return `no handler for ${key}`;
        },
      };
    }
    const payload = handler(body);
    return {
      ok: true,
      status: 200,
      statusText: "OK",
      async json() {
        return payload;
      },
      async text() {
        return JSON.stringify(payload);
      },
    };
  };
  return { fetchImpl, calls };
}

test("DA-U1 loadDurableMemoryConfig", () => {
  const dir = join(tmpdir(), `yal-dm-${Date.now()}`);
  mkdirSync(join(dir, ".yal"), { recursive: true });
  assert.equal(loadDurableMemoryConfig(dir), null);
  writeFileSync(
    join(dir, ".yal", "memory_service.json"),
    JSON.stringify({
      base_url: "http://memory:8090",
      scope: { tenant: "t", subject: "s", namespace: "n" },
      session_id: "sess-1",
    }),
  );
  const cfg = loadDurableMemoryConfig(dir);
  assert.equal(cfg?.base_url, "http://memory:8090");
  assert.equal(cfg?.session_id, "sess-1");
  rmSync(dir, { recursive: true, force: true });
});

test("DA-U2 pushEvent is sync and does not call fetch", () => {
  const { fetchImpl, calls } = mockFetch({});
  const adapter = new DurableMemoryAdapter({
    config: {
      base_url: "http://memory:8090",
      scope: { tenant: "t", subject: "s", namespace: "n" },
    },
    fetchImpl,
  });
  adapter.pushEvent("user/message", { text: "hi" });
  adapter.pushEvent("assistant/message", { text: "yo" });
  assert.equal(adapter.pendingCount, 2);
  assert.equal(calls.length, 0);
});

test("DA-U3 flush drains buffer via client", async () => {
  const { fetchImpl, calls } = mockFetch({
    "POST /v1/sessions": () => ({ id: "s1", format_version: 1 }),
    "POST /v1/sessions/s1/events:buffer": () => ({ buffered: 1, seqs: [0] }),
    "POST /v1/sessions/s1/flush": () => ({ last_seq: 0 }),
  });
  const adapter = new DurableMemoryAdapter({
    config: {
      base_url: "http://memory:8090",
      scope: { tenant: "t", subject: "s", namespace: "n" },
    },
    fetchImpl,
  });
  await adapter.start();
  adapter.pushEvent("user/message", { text: "a" });
  const result = await adapter.flush();
  assert.equal(result.flushed, 1);
  assert.equal(result.last_seq, 0);
  assert.equal(adapter.pendingCount, 0);
  assert.ok(calls.some((c) => c.url.includes("events:buffer")));
  assert.ok(calls.some((c) => c.url.endsWith("/flush")));
});

test("DA-U4 start createSession when no session_id", async () => {
  const { fetchImpl } = mockFetch({
    "POST /v1/sessions": () => ({ id: "created-9", format_version: 1 }),
  });
  const adapter = new DurableMemoryAdapter({
    config: {
      base_url: "http://memory:8090",
      scope: { tenant: "t", subject: "s", namespace: "n" },
    },
    fetchImpl,
  });
  const id = await adapter.start();
  assert.equal(id, "created-9");
  assert.equal(adapter.id, "created-9");
});

test("DA-U5 resume returns loaded events", async () => {
  const { fetchImpl } = mockFetch({
    "GET /v1/sessions/s-resume": () => ({
      id: "s-resume",
      status: "interrupted",
      repaired: true,
      events: [
        { type: "tool/call", seq: 0, time: 1, data: { call_id: "c" } },
        {
          type: "tool/result",
          seq: 1,
          time: 2,
          data: { call_id: "c", status: "interrupted", recovered: true },
        },
        { type: "turn/end", seq: 2, time: 3, data: { kind: "interrupted" } },
      ],
    }),
  });
  const adapter = new DurableMemoryAdapter({
    config: {
      base_url: "http://memory:8090",
      scope: { tenant: "t", subject: "s", namespace: "n" },
      session_id: "s-resume",
    },
    fetchImpl,
  });
  const loaded = await adapter.resume();
  assert.equal(loaded.repaired, true);
  assert.equal(loaded.events.at(-1)?.type, "turn/end");
});

test("DA-U6 adapter modules have no extraction/ranking logic", async () => {
  const { readFileSync } = await import("node:fs");
  const { fileURLToPath } = await import("node:url");
  const { dirname, join: j } = await import("node:path");
  const here = dirname(fileURLToPath(import.meta.url));
  // When run from dist, sources are sibling .js; also check .ts if present
  const files = ["index.js", "client.js", "buffer.js", "config.js"].map((f) =>
    j(here, f),
  );
  const banned = /\b(embed|embedding|bm25|rankFusion|extractMemor)/i;
  for (const f of files) {
    const text = readFileSync(f, "utf8");
    assert.equal(banned.test(text), false, `banned logic in ${f}`);
  }
});

test("DA-U7 sync push latency budget", () => {
  const buf = new AdapterEventBuffer();
  const t0 = performance.now();
  for (let i = 0; i < 1000; i++) {
    buf.push("user/message", { i });
  }
  const ms = performance.now() - t0;
  assert.ok(ms < 100, `push 1000 too slow: ${ms}ms`);
});

test("registerDurableMemory noops without bind", () => {
  const dir = join(tmpdir(), `yal-dm-reg-${Date.now()}`);
  mkdirSync(dir, { recursive: true });
  const handlers: Record<string, unknown[]> = {};
  const api = {
    on(event: string, handler: (...args: unknown[]) => unknown) {
      handlers[event] = handlers[event] ?? [];
      handlers[event].push(handler);
    },
  };
  const result = registerDurableMemory(api as never, dir);
  assert.equal(result, null);
  assert.equal(Object.keys(handlers).length, 0);
  rmSync(dir, { recursive: true, force: true });
});

test("DurableMemoryClient strips trailing slash", async () => {
  const { fetchImpl, calls } = mockFetch({
    "GET /v1/sessions/x": () => ({
      id: "x",
      status: "active",
      repaired: false,
      events: [],
    }),
  });
  const client = new DurableMemoryClient("http://memory:8090/", fetchImpl);
  await client.load("x");
  assert.equal(calls[0]?.url, "http://memory:8090/v1/sessions/x");
});
