/**
 * Runs as the user sees them: which one Stop should cancel, and how to label each.
 *
 * Every message gets a run id at once (its turn_id). The server keeps each run's lifecycle
 * (queued → in_progress / requires_action → completed, failed, cancelled or incomplete), so
 * Stop cancels a run by id. Cancel is safe to repeat, and survives a reload.
 */

import type { Run } from '@/lib/api/types/session.types';

export const RUN_ENDED = new Set<Run['status']>(['completed', 'failed', 'cancelled', 'incomplete']);

export const RUN_LABEL: Record<Run['status'], string> = {
  queued: 'Queued',
  in_progress: 'Running',
  requires_action: 'Waiting for approval',
  completed: 'Completed',
  failed: 'Failed',
  cancelled: 'Cancelled',
  incomplete: 'Stopped by a limit',
};

/** The run Stop cancels: the one going now (runs come newest first; at most one is going). */
export function runToStop(runs: Run[]): Run | undefined {
  return runs.find((r) => r.status === 'in_progress' || r.status === 'requires_action');
}

/** How long a run took (or has been going, given `now`): "820 ms", "4.2 s", "3 m 05 s". */
export function runDuration(run: Run, now = Date.now()): string | null {
  if (run.started_at_ms == null) return null;
  const ms = Math.max(0, (run.ended_at_ms ?? now) - run.started_at_ms);
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)} m ${String(s % 60).padStart(2, '0')} s`;
}
