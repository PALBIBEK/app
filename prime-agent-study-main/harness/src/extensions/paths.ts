/** Shared `.yal` path helper for harness extensions. */
import { join } from "node:path";

export function yalDir(cwd: string = process.cwd()): string {
  return join(cwd, ".yal");
}
