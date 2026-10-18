/**
 * Memory profile from supervisor-materialized `.yal/memory_profile.json`.
 */
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { yalDir } from "../paths.js";

export type MemoryProfileBind = {
  id: string;
  digest: string;
  compaction: {
    enabled: boolean;
    reserve_tokens: number;
    keep_recent_tokens: number;
    pressure_ratio: number;
    summarization_model?: string;
  };
  recall: {
    enabled: boolean;
    backend: "none" | "githi_markdown" | "mcp";
    mcp_server_url?: string;
  };
};

export function loadMemoryProfile(cwd: string = process.cwd()): MemoryProfileBind | null {
  const path = join(yalDir(cwd), "memory_profile.json");
  if (!existsSync(path)) {
    return null;
  }
  return JSON.parse(readFileSync(path, "utf8")) as MemoryProfileBind;
}

export function summarizationModel(cwd: string = process.cwd()): string | null {
  const profile = loadMemoryProfile(cwd);
  if (!profile?.compaction.enabled) {
    return null;
  }
  return profile.compaction.summarization_model ?? null;
}