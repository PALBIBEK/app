/**
 * Model gateway route from supervisor-materialized `.yal/plug_bindings.json`.
 * Configures OpenAI-compatible provider base URL for the harness agent loop.
 */
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { yalDir } from "./paths.js";

export type PlugBindings = {
  gateway_base_url?: string;
  model_route?: string;
  mcp_server_url?: string;
  acp_base_url?: string;
};

export function loadPlugBindings(cwd: string = process.cwd()): PlugBindings {
  const path = join(yalDir(cwd), "plug_bindings.json");
  if (!existsSync(path)) {
    return {};
  }
  return JSON.parse(readFileSync(path, "utf8")) as PlugBindings;
}

export function gatewayOpenAiBaseUrl(cwd: string = process.cwd()): string | null {
  const plug = loadPlugBindings(cwd);
  const raw = plug.gateway_base_url?.trim();
  if (!raw) {
    return null;
  }
  const base = raw.replace(/\/$/, "");
  return base.endsWith("/v1") ? base : `${base}/v1`;
}
