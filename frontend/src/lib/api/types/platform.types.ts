/**
 * Platform readiness types (D2) — mirror BFF PlatformStatusResponse.
 */

export interface ComponentStatus {
  ready: boolean;
  status: string;
  reason: string | null;
}

export interface WorkerAlert {
  code: 'no_workers' | 'backlog' | 'at_capacity';
  message: string;
}

/** The agent worker fleet in counts: `down` means no worker (work waits; nothing is lost). */
export interface WorkersStatus {
  status: 'ok' | 'degraded' | 'down';
  polling: number;
  slots_busy: number;
  slots_max: number;
  backlog: number;
  backlog_age_s: number;
  alerts: WorkerAlert[];
}

export interface PlatformStatus {
  ready: boolean;
  run_allowed: boolean;
  checked_at: string;
  components: Record<string, ComponentStatus>;
  residual?: string[];
  /** Absent when the supervisor does not report its workers. */
  workers?: WorkersStatus | null;
}
