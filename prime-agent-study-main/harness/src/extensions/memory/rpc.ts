/**
 * Invoke session compaction via supervisor HTTP RPC (`.yal/memory_rpc.json`).
 */
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { yalDir } from "../paths.js";

export type MemoryRpcConfig = {
  compact_url: string;
  recall_url?: string;
  tenant_id?: string;
};

export type CompactMemoryRequest = {
  reason?: "manual" | "threshold" | "overflow";
  first_kept_entry_id: string;
  messages_to_summarize: Array<Record<string, unknown>>;
  tokens_before: number;
  previous_summary?: string | null;
  context_window?: number;
  reserve_tokens?: number;
};

export type CompactMemoryResult = {
  compaction_id: string;
  stop_reason: string;
  summary?: string | null;
  tokens_before?: number | null;
  tokens_after?: number | null;
  summary_digest?: string | null;
};

export function loadMemoryRpcConfig(cwd: string = process.cwd()): MemoryRpcConfig | null {
  const path = join(yalDir(cwd), "memory_rpc.json");
  if (!existsSync(path)) {
    return null;
  }
  return JSON.parse(readFileSync(path, "utf8")) as MemoryRpcConfig;
}

export async function compactViaSupervisor(
  body: CompactMemoryRequest,
  cwd: string = process.cwd(),
): Promise<CompactMemoryResult> {
  const cfg = loadMemoryRpcConfig(cwd);
  if (!cfg?.compact_url) {
    throw new Error(
      `missing ${join(yalDir(cwd), "memory_rpc.json")}: composition sessions require supervisor memory RPC`,
    );
  }
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (cfg.tenant_id) {
    headers["X-Tenant-Id"] = cfg.tenant_id;
  }
  const res = await fetch(cfg.compact_url, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  const payload = (await res.json()) as CompactMemoryResult | { detail?: unknown };
  if (!res.ok) {
    const detail =
      typeof payload === "object" && payload && "detail" in payload
        ? String(payload.detail)
        : res.statusText;
    throw new Error(`memory compact failed (${res.status}): ${detail}`);
  }
  return payload as CompactMemoryResult;
}

export type RecallMemoryRequest = {
  user_prompt: string;
};

export type RecallMemoryResult = {
  block_id: string;
  injected: boolean;
  source: string;
  text?: string | null;
  token_estimate?: number;
};

export async function recallViaSupervisor(
  body: RecallMemoryRequest,
  cwd: string = process.cwd(),
): Promise<RecallMemoryResult> {
  const cfg = loadMemoryRpcConfig(cwd);
  if (!cfg?.recall_url) {
    throw new Error(
      `missing recall_url in ${join(yalDir(cwd), "memory_rpc.json")}: recall requires supervisor memory RPC`,
    );
  }
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (cfg.tenant_id) {
    headers["X-Tenant-Id"] = cfg.tenant_id;
  }
  const res = await fetch(cfg.recall_url, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  const payload = (await res.json()) as RecallMemoryResult | { detail?: unknown };
  if (!res.ok) {
    const detail =
      typeof payload === "object" && payload && "detail" in payload
        ? String(payload.detail)
        : res.statusText;
    throw new Error(`memory recall failed (${res.status}): ${detail}`);
  }
  return payload as RecallMemoryResult;
}
