/**
 * Views across the user's sessions, and the operating pages (BFF /approvals,
 * /sandboxes, /usage, /runners, /secrets, /ops, /catalog/{kind}/check).
 * Session ids are this product's ids, never the supervisor's.
 */

export interface InboxItem {
  approval_id: string;
  session_id: string;
  session_title: string | null;
  harness_id: string | null;
  harness_name: string | null;
  prompt: string;
  options: string[];
  created_at_ms: number | null;
  expires_at_ms: number | null;
}

export type SandboxState = 'running' | 'stopped' | 'lost' | 'not_started';

export interface SandboxItem {
  session_id: string;
  session_title: string | null;
  harness_name: string | null;
  hand: string;
  state: SandboxState;
  sandbox_id: string | null;
  provider: string | null;
  since_ms: number | null;
  cause: string | null;
  last_checkpoint_ms: number | null;
  checkpoint_files: number;
  processes: string[];
  reason: string | null;
  logs_ref: string | null;
  lease_holder: string | null;
  lease_expires_ms: number | null;
}

export type UsageBy = 'day' | 'agent' | 'session';

export interface UsageRow {
  key: string;
  label: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost: number;
  model_calls: number;
  sessions: number;
}

export interface UsageReport {
  since_ms: number;
  until_ms: number;
  by: UsageBy;
  rows: UsageRow[];
  total: UsageRow;
}

export interface Runner {
  name: string;
  online: boolean;
}

export interface RunnerToken {
  name: string;
  token: string;
}

export type ArtifactKind = 'prompt_pack' | 'tool_set' | 'composition' | 'sandbox_profile' | 'memory_profile';

export interface PublishCheck {
  check: string;
  ok: boolean;
  message: string;
  code: string | null;
}

export interface PublishCheckResult {
  ok: boolean;
  digest: string | null;
  checks: PublishCheck[];
}

/** The worker fleet, as the supervisor reports it; shapes vary by version, so fields are loose. */
export type FleetReport = Record<string, unknown>;
