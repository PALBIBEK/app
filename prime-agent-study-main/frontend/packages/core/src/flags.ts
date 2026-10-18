export type Flags = {
  /** Experimental builder surfaces. */
  builder: boolean;
  /** Dev kitchen-sink route (excluded from production builds by the shell). */
  kitchenSink: boolean;
};

type EnvBag = Record<string, string | boolean | undefined>;

function readEnv(): EnvBag {
  try {
    const meta = import.meta as ImportMeta & { env?: EnvBag };
    if (meta.env) return meta.env;
  } catch {
    // import.meta may be unavailable in some Node test runners
  }
  const g = globalThis as typeof globalThis & {
    process?: { env?: EnvBag };
  };
  return g.process?.env ?? {};
}

function parseBool(value: string | boolean | undefined, fallback: boolean): boolean {
  if (typeof value === "boolean") return value;
  if (value === undefined) return fallback;
  const normalized = value.trim().toLowerCase();
  if (normalized === "1" || normalized === "true" || normalized === "yes") return true;
  if (normalized === "0" || normalized === "false" || normalized === "no") return false;
  return fallback;
}

/**
 * Env-only typed flags (Phase 0 decision). Call sites use `flags.builder`
 * so a future provider can swap the reader without touching consumers.
 */
export const flags: Flags = {
  builder: parseBool(readEnv().VITE_FLAG_BUILDER, false),
  kitchenSink: parseBool(readEnv().VITE_FLAG_KITCHEN_SINK, false),
};
