/**
 * Subagent child commands via supervisor session RPC (`.yal/subagent_rpc.json`).
 */
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { yalDir } from "../paths.js";

export type SubagentRpcConfig = {
  base_url: string;
  session_id: string;
  tenant_id?: string;
  spawn_path: string;
  fork_path: string;
  list_children_path: string;
  providers: string[];
  default_provider: string;
};

export type SpawnChildResult = {
  child_session_id: string;
  text?: string | null;
  status?: string | null;
  run_id?: string | null;
};

export function loadSubagentRpcConfig(cwd: string = process.cwd()): SubagentRpcConfig | null {
  const path = join(yalDir(cwd), "subagent_rpc.json");
  if (!existsSync(path)) {
    return null;
  }
  return JSON.parse(readFileSync(path, "utf8")) as SubagentRpcConfig;
}

function rpcUrl(cfg: SubagentRpcConfig, relPath: string): string {
  return `${cfg.base_url.replace(/\/$/, "")}${relPath}`;
}

function rpcHeaders(cfg: SubagentRpcConfig): Record<string, string> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (cfg.tenant_id) {
    headers["X-Tenant-Id"] = cfg.tenant_id;
  }
  return headers;
}

export async function spawnChild(
  prompt: string,
  opts: { description?: string; provider?: string } = {},
  cwd: string = process.cwd(),
): Promise<SpawnChildResult> {
  const cfg = loadSubagentRpcConfig(cwd);
  if (!cfg) {
    throw new Error(
      `missing ${join(yalDir(cwd), "subagent_rpc.json")}: composition sessions require supervisor subagent RPC`,
    );
  }
  const res = await fetch(rpcUrl(cfg, cfg.spawn_path), {
    method: "POST",
    headers: rpcHeaders(cfg),
    body: JSON.stringify({
      prompt,
      description: opts.description ?? "",
      provider: opts.provider ?? cfg.default_provider,
    }),
  });
  if (!res.ok) {
    throw new Error(`subagent spawn failed: ${res.status} ${await res.text()}`);
  }
  return (await res.json()) as SpawnChildResult;
}

export async function listChildren(cwd: string = process.cwd()) {
  const cfg = loadSubagentRpcConfig(cwd);
  if (!cfg) {
    return [];
  }
  const res = await fetch(rpcUrl(cfg, cfg.list_children_path), {
    headers: rpcHeaders(cfg),
  });
  if (!res.ok) {
    throw new Error(`list_children failed: ${res.status} ${await res.text()}`);
  }
  return res.json();
}
