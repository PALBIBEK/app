/**
 * Phase 0.5 — memory hooks prefer supervisor RPC when bind present.
 * Run: npm run build && node --test dist/extensions/memory/hooks.test.js
 */
import { mkdirSync, writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  buildBeforeCompactHandler,
  buildBeforeTurnRecallHandler,
  registerMemoryHooks,
} from "./hooks.js";

function writeYal(
  dir: string,
  opts: {
    profile?: Record<string, unknown>;
    rpc?: Record<string, unknown>;
  },
): void {
  const yal = join(dir, ".yal");
  mkdirSync(yal, { recursive: true });
  if (opts.profile) {
    writeFileSync(join(yal, "memory_profile.json"), JSON.stringify(opts.profile));
  }
  if (opts.rpc) {
    writeFileSync(join(yal, "memory_rpc.json"), JSON.stringify(opts.rpc));
  }
}

const baseProfile = {
  id: "default",
  digest: "sha256:x",
  compaction: {
    enabled: true,
    reserve_tokens: 16384,
    keep_recent_tokens: 20000,
    pressure_ratio: 0.8,
  },
  recall: { enabled: false, backend: "none" as const },
};

test("before_compact uses supervisor RPC when compact_url present", async () => {
  const dir = mkdtempSync(join(tmpdir(), "mem-hooks-"));
  let called = "";
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    called = String(input);
    return new Response(
      JSON.stringify({
        compaction_id: "c1",
        stop_reason: "ok",
        summary: "rpc-summary",
        tokens_before: 100,
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  }) as typeof fetch;

  try {
    writeYal(dir, {
      profile: baseProfile,
      rpc: { compact_url: "http://supervisor.test/v1/sessions/s1/memory/compact" },
    });
    const handler = buildBeforeCompactHandler(dir);
    const result = await handler({
      reason: "threshold",
      preparation: {
        messagesToSummarize: [{ role: "user", content: "hi" }],
        tokensBefore: 100,
        firstKeptEntryId: "e1",
      },
    });
    assert.equal(result?.compaction?.summary, "rpc-summary");
    assert.match(called, /memory\/compact/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("before_turn recall miss returns injected false without throwing", async () => {
  const dir = mkdtempSync(join(tmpdir(), "mem-recall-"));
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () => {
    return new Response(
      JSON.stringify({
        block_id: "b1",
        injected: false,
        source: "semantic",
        text: null,
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  }) as typeof fetch;

  try {
    writeYal(dir, {
      profile: {
        ...baseProfile,
        recall: { enabled: true, backend: "githi_markdown" },
      },
      rpc: {
        compact_url: "http://supervisor.test/v1/sessions/s1/memory/compact",
        recall_url: "http://supervisor.test/v1/sessions/s1/memory/recall",
      },
    });
    const handler = buildBeforeTurnRecallHandler(dir);
    const result = await handler({ userPrompt: "hello" });
    assert.equal(result?.recall?.injected, false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("registerMemoryHooks wires compaction when profile enabled", () => {
  const dir = mkdtempSync(join(tmpdir(), "mem-reg-"));
  writeYal(dir, {
    profile: baseProfile,
    rpc: { compact_url: "http://supervisor.test/compact" },
  });
  const events: string[] = [];
  const api = {
    on(event: string) {
      events.push(event);
    },
  };
  const result = registerMemoryHooks(api as never, dir);
  assert.equal(result.compaction, true);
  assert.equal(result.recall, false);
  assert.deepEqual(events, ["session_before_compact"]);
});
