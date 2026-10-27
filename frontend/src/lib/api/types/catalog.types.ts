/**
 * What a session runs (its pinned composition, part by part) and how much each agent
 * version is used. Read from the catalog through the BFF; digests are named as the
 * agent's version numbers where they are one of its releases.
 */

export type PartStatus = 'offered' | 'deprecated' | 'unknown' | string;

export interface PinnedPart {
  kind: 'prompt_pack' | 'tool_set' | 'sandbox_profile' | 'memory_profile' | string;
  /** `prompt`, `tools`, `memory`, or `hand:<name>` for a sandbox (`hand:default` the main one). */
  role: string;
  artifact_id: string;
  digest: string;
  semver: string | null;
  status: PartStatus;
  settings: Record<string, unknown>;
}

export interface SessionPins {
  composition_id: string;
  composition_digest: string;
  composition_semver: string | null;
  composition_status: PartStatus;
  /** The agent version this composition was published as (null when not one of its releases). */
  release_version: number | null;
  harness_version: string;
  model_route: string | null;
  mcp_configured: boolean;
  acp_configured: boolean;
  parts: PinnedPart[];
}

export interface VersionUsage {
  version: number | null;
  digest: string;
  sessions: number;
}

export interface HarnessUsage {
  session_count: number;
  active_sessions: number;
  first_used_at: string | null;
  last_used_at: string | null;
  /** Sessions per pinned version, most recently used first. */
  versions: VersionUsage[];
}
