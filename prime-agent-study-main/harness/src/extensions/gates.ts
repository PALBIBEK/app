/**
 * Policy gates (budget / loop / stall) — supervisor remains authority.
 * Extension may load `.yal/pins.json` for digest context; no local catalog.
 */
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { yalDir } from "./paths.js";

export function registerGates(cwd: string = process.cwd()): Record<string, string> | null {
  const path = join(yalDir(cwd), "pins.json");
  if (!existsSync(path)) {
    return null;
  }
  return JSON.parse(readFileSync(path, "utf8")) as Record<string, string>;
}
