/**
 * Pi memory hooks — prefer supervisor RPC when `.yal/memory_rpc.json` exists;
 * gateway summarization is fallback only.
 */
import { loadMemoryProfile } from "./profile.js";
import {
  compactViaSupervisor,
  loadMemoryRpcConfig,
  recallViaSupervisor,
} from "./rpc.js";
import { summarizeViaGateway } from "./summarize.js";

export type SessionBeforeCompactEvent = {
  reason: "manual" | "threshold" | "overflow";
  preparation: {
    messagesToSummarize: Array<Record<string, unknown>>;
    tokensBefore: number;
    firstKeptEntryId: string;
    isSplitTurn?: boolean;
  };
};

export type SessionBeforeTurnEvent = {
  userPrompt?: string;
  prompt?: string;
};

export type ExtensionAPI = {
  on(
    event: "session_before_compact" | "session_before_turn",
    handler: (event: never) => Promise<unknown>,
  ): void;
};

export type CompactionHookResult = {
  summary: string;
  firstKeptEntryId: string;
  tokensBefore: number;
};

export type RecallHookResult = {
  text: string;
  source: string;
  injected: boolean;
};

export function buildBeforeCompactHandler(cwd: string = process.cwd()) {
  return async (
    event: SessionBeforeCompactEvent,
  ): Promise<{ compaction: CompactionHookResult } | void> => {
    const profile = loadMemoryProfile(cwd);
    if (!profile?.compaction.enabled) {
      return;
    }
    const rpc = loadMemoryRpcConfig(cwd);
    try {
      if (rpc?.compact_url) {
        const result = await compactViaSupervisor(
          {
            reason: event.reason,
            first_kept_entry_id: event.preparation.firstKeptEntryId,
            messages_to_summarize: event.preparation.messagesToSummarize,
            tokens_before: event.preparation.tokensBefore,
          },
          cwd,
        );
        if (!result.summary) {
          return;
        }
        return {
          compaction: {
            summary: result.summary,
            firstKeptEntryId: event.preparation.firstKeptEntryId,
            tokensBefore: result.tokens_before ?? event.preparation.tokensBefore,
          },
        };
      }
      const summary = await summarizeViaGateway(
        event.preparation.messagesToSummarize,
        cwd,
      );
      return {
        compaction: {
          summary,
          firstKeptEntryId: event.preparation.firstKeptEntryId,
          tokensBefore: event.preparation.tokensBefore,
        },
      };
    } catch {
      // Degrade: let Pi built-in compaction proceed.
      return;
    }
  };
}

export function buildBeforeTurnRecallHandler(cwd: string = process.cwd()) {
  return async (
    event: SessionBeforeTurnEvent,
  ): Promise<{ recall?: RecallHookResult } | void> => {
    const profile = loadMemoryProfile(cwd);
    if (!profile?.recall.enabled) {
      return;
    }
    const rpc = loadMemoryRpcConfig(cwd);
    if (!rpc?.recall_url) {
      return;
    }
    const userPrompt = event.userPrompt ?? event.prompt ?? "";
    try {
      const result = await recallViaSupervisor({ user_prompt: userPrompt }, cwd);
      // Miss still returns injected=false — never fail the turn.
      if (!result.injected || !result.text) {
        return { recall: { text: "", source: result.source, injected: false } };
      }
      return {
        recall: {
          text: result.text,
          source: result.source,
          injected: true,
        },
      };
    } catch {
      return { recall: { text: "", source: "error", injected: false } };
    }
  };
}

export function registerMemoryCompaction(
  pi: ExtensionAPI,
  cwd: string = process.cwd(),
): boolean {
  const profile = loadMemoryProfile(cwd);
  if (!profile?.compaction.enabled) {
    return false;
  }
  pi.on(
    "session_before_compact",
    buildBeforeCompactHandler(cwd) as (event: never) => Promise<unknown>,
  );
  return true;
}

export function registerMemoryRecall(
  pi: ExtensionAPI,
  cwd: string = process.cwd(),
): boolean {
  const profile = loadMemoryProfile(cwd);
  if (!profile?.recall.enabled) {
    return false;
  }
  if (!loadMemoryRpcConfig(cwd)?.recall_url) {
    return false;
  }
  pi.on(
    "session_before_turn",
    buildBeforeTurnRecallHandler(cwd) as (event: never) => Promise<unknown>,
  );
  return true;
}

/** Register compaction + optional recall hooks from materialized binds. */
export function registerMemoryHooks(
  pi: ExtensionAPI,
  cwd: string = process.cwd(),
): { compaction: boolean; recall: boolean } {
  return {
    compaction: registerMemoryCompaction(pi, cwd),
    recall: registerMemoryRecall(pi, cwd),
  };
}
