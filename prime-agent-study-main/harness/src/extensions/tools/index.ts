/**
 * Tool descriptors from supervisor-materialized `.yal/tools.json`.
 * Production ToolSet is catalog digests — not harness YAML `tools:` recovery map.
 */
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { yalDir } from "../paths.js";

export type BoundTool = {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
};

export function loadBoundTools(cwd: string = process.cwd()): BoundTool[] {
  const path = join(yalDir(cwd), "tools.json");
  if (!existsSync(path)) {
    throw new Error(
      `missing ${path}: composition sessions require supervisor materialize_harness_binds`,
    );
  }
  const raw = JSON.parse(readFileSync(path, "utf8")) as unknown;
  if (!Array.isArray(raw)) {
    throw new Error(`${path} must be a JSON array`);
  }
  return raw as BoundTool[];
}

export function registerTools(cwd: string = process.cwd()): BoundTool[] {
  return loadBoundTools(cwd);
}
