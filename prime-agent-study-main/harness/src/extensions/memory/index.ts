export { loadMemoryProfile, summarizationModel } from "./profile.js";
export type { MemoryProfileBind } from "./profile.js";
export { SUMMARY_MARKER, summarizeViaGateway } from "./summarize.js";
export {
  buildBeforeCompactHandler,
  buildBeforeTurnRecallHandler,
  registerMemoryCompaction,
  registerMemoryHooks,
  registerMemoryRecall,
} from "./hooks.js";
export type {
  CompactionHookResult,
  ExtensionAPI,
  RecallHookResult,
  SessionBeforeCompactEvent,
  SessionBeforeTurnEvent,
} from "./hooks.js";
export {
  compactViaSupervisor,
  loadMemoryRpcConfig,
  recallViaSupervisor,
} from "./rpc.js";
export type {
  CompactMemoryRequest,
  CompactMemoryResult,
  MemoryRpcConfig,
  RecallMemoryRequest,
  RecallMemoryResult,
} from "./rpc.js";
export {
  AdapterEventBuffer,
  DurableMemoryAdapter,
  DurableMemoryClient,
  loadDurableMemoryConfig,
  registerDurableMemory,
} from "./durable/index.js";
export type {
  DurableMemoryAdapterOptions,
  DurableMemoryConfig,
  DurableMemoryScope,
  DurableExtensionAPI,
  LoadedSession,
  SessionEventWire,
} from "./durable/index.js";

import { loadMemoryProfile } from "./profile.js";
import { summarizationModel } from "./profile.js";
import { loadDurableMemoryConfig } from "./durable/config.js";

export function inspectMemoryBind(cwd: string = process.cwd()) {
  const profile = loadMemoryProfile(cwd);
  const durable = (() => {
    try {
      return loadDurableMemoryConfig(cwd);
    } catch {
      return null;
    }
  })();
  if (!profile && !durable) {
    return { ok: true, has_profile: false, has_durable: false };
  }
  return {
    ok: true,
    has_profile: Boolean(profile),
    has_durable: Boolean(durable),
    compaction_enabled: profile?.compaction.enabled ?? false,
    summarization_model: profile ? summarizationModel(cwd) : null,
    profile_id: profile?.id ?? null,
    durable_base_url: durable?.base_url ?? null,
    durable_session_id: durable?.session_id ?? null,
  };
}

