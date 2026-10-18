/**
 * System / developer prompt injection for the harness.
 * Reads supervisor-materialized `.yal/system_prompt.txt` (QUEUE harness-binders).
 */
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { yalDir } from "./paths.js";

export function loadBoundSystemPrompt(cwd: string = process.cwd()): string {
  const path = join(yalDir(cwd), "system_prompt.txt");
  if (!existsSync(path)) {
    throw new Error(
      `missing ${path}: composition sessions require supervisor materialize_harness_binds`,
    );
  }
  return readFileSync(path, "utf8");
}

export function registerPrompt(cwd: string = process.cwd()): string {
  return loadBoundSystemPrompt(cwd);
}
