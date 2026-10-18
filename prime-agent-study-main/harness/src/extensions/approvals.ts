/**
 * Approval bridge — supervisor ApprovalBroker remains authority.
 * Reads sandbox profile for composition-level approval_default when present.
 */
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { yalDir } from "./paths.js";

export type SandboxProfileBind = {
  approval_default?: "ask" | "never";
  file_effects?: string;
};

export function loadSandboxProfileBind(
  cwd: string = process.cwd(),
): SandboxProfileBind | null {
  const path = join(yalDir(cwd), "sandbox_profile.json");
  if (!existsSync(path)) {
    return null;
  }
  return JSON.parse(readFileSync(path, "utf8")) as SandboxProfileBind;
}

export function registerApprovals(cwd: string = process.cwd()): SandboxProfileBind | null {
  return loadSandboxProfileBind(cwd);
}

export function shouldAutoApprove(cwd: string = process.cwd()): boolean {
  const profile = loadSandboxProfileBind(cwd);
  return profile?.approval_default === "never";
}
