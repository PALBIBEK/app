/**
 * Durable Memory Service bind — `.yal/memory_service.json`.
 * Separate from ContextMemory `.yal/memory_rpc.json` (compaction/recall).
 */
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { yalDir } from "../../paths.js";

export type DurableMemoryScope = {
  tenant: string;
  subject: string;
  namespace: string;
};

export type DurableMemoryConfig = {
  base_url: string;
  scope: DurableMemoryScope;
  /** Pre-created / resume id; omit to create on start */
  session_id?: string;
  lease_owner?: string;
};

export function loadDurableMemoryConfig(
  cwd: string = process.cwd(),
): DurableMemoryConfig | null {
  const path = join(yalDir(cwd), "memory_service.json");
  if (!existsSync(path)) {
    return null;
  }
  const raw = JSON.parse(readFileSync(path, "utf8")) as DurableMemoryConfig;
  if (!raw?.base_url || !raw?.scope?.tenant || !raw?.scope?.subject || !raw?.scope?.namespace) {
    throw new Error(
      `invalid ${path}: require base_url and scope.{tenant,subject,namespace}`,
    );
  }
  return raw;
}
