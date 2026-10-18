/**
 * Invoke catalog tools via supervisor session RPC (`.yal/tool_invoke.json`).
 */
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { yalDir } from "../paths.js";

export type ToolInvokeConfig = {
  url: string;
  tenant_id?: string;
};

export type ToolInvokeResult = {
  ok: boolean;
  payload?: unknown;
  error?: string;
  code?: string;
  tool_call_id?: string;
};

export function loadToolInvokeConfig(cwd: string = process.cwd()): ToolInvokeConfig | null {
  const path = join(yalDir(cwd), "tool_invoke.json");
  if (!existsSync(path)) {
    return null;
  }
  return JSON.parse(readFileSync(path, "utf8")) as ToolInvokeConfig;
}

export async function invokeBoundTool(
  name: string,
  args: Record<string, unknown>,
  cwd: string = process.cwd(),
): Promise<ToolInvokeResult> {
  const cfg = loadToolInvokeConfig(cwd);
  if (!cfg?.url) {
    throw new Error(
      `missing ${join(yalDir(cwd), "tool_invoke.json")}: composition sessions require supervisor tool RPC`,
    );
  }
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (cfg.tenant_id) {
    headers["X-Tenant-Id"] = cfg.tenant_id;
  }
  const res = await fetch(cfg.url, {
    method: "POST",
    headers,
    body: JSON.stringify({ name, arguments: args }),
  });
  const body = (await res.json()) as ToolInvokeResult;
  if (!res.ok) {
    return {
      ok: false,
      error: typeof body === "object" && body && "detail" in body ? String((body as { detail: unknown }).detail) : res.statusText,
      code: "HTTP_ERROR",
    };
  }
  return body;
}
