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

/** One worker's heartbeat. */
export interface WorkerBeat {
  worker_id: string;
  host: string;
  task_queue: string;
  home_queue: string;
  max_runs: number;
  busy: number;
  started_at_ms: number;
  last_seen_ms: number;
  version?: string;
}

/** The session queue's health and who serves it (GET /ops/workers). */
export interface FleetStatus {
  status: 'ok' | 'degraded' | 'down' | string;
  task_queue: string;
  polling: number;
  workers: WorkerBeat[];
  slots_max: number;
  slots_busy: number;
  backlog: number;
  backlog_age_s: number;
  add_rate: number;
  dispatch_rate: number;
  alerts: { code: string; message: string }[];
  checked_at_ms: number;
}

/** The worker deployment's versions (GET /ops/rollout); `versioned` false when workers are unversioned. */
export interface RolloutStatus {
  versioned: boolean;
  deployment?: string;
  current_build?: string | null;
  ramping_build?: string | null;
  ramping_percentage?: number;
  versions?: { build_id: string; status: string; drainage?: string }[];
}
