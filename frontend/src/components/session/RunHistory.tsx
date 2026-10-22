'use client';

/**
 * The session's runs, newest first: what happened to each message (status, how long it took,
 * steps, tokens and cost), with Cancel on one that is queued or going. Refreshes while any run
 * has not ended, and when `refreshKey` changes (a message was sent, a run ended).
 */

import { useCallback, useEffect, useState } from 'react';
import { CircleNotch, X } from '@phosphor-icons/react';
import { toast } from 'sonner';

import { sessionsAPI } from '@/lib/api/endpoints/sessions';
import type { Run } from '@/lib/api/types/session.types';
import { RUN_ENDED, RUN_LABEL, runDuration } from '@/lib/session/runs';
import { cn } from '@/lib/utils';

const TONE: Record<Run['status'], string> = {
  queued: 'bg-slate-100 text-slate-600',
  in_progress: 'bg-primary-50 text-primary-700',
  requires_action: 'bg-amber-50 text-amber-700',
  completed: 'bg-secondary-50 text-secondary-700',
  failed: 'bg-red-50 text-red-700',
  cancelled: 'bg-slate-100 text-slate-500',
  incomplete: 'bg-amber-50 text-amber-700',
};

export interface RunHistoryProps {
  sessionId: string;
  /** Reload when this changes (a message was sent, a run started or ended). */
  refreshKey: number;
  /** Told when a run here is cancelled before it started (the chat marks its message). */
  onCancelled?: (runId: string) => void;
}

export function RunHistory({ sessionId, refreshKey, onCancelled }: RunHistoryProps): JSX.Element {
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setRuns(await sessionsAPI.runs(sessionId, 20));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load the runs');
    }
  }, [sessionId]);

  useEffect(() => void load(), [load, refreshKey]);
  const going = runs?.some((r) => !RUN_ENDED.has(r.status)) ?? false;
  useEffect(() => {
    if (!going) return undefined;
    const timer = setInterval(() => void load(), 2000);
    return () => clearInterval(timer);
  }, [going, load]);

  async function cancel(run: Run): Promise<void> {
    try {
      const after = await sessionsAPI.cancelRun(sessionId, run.run_id);
      setRuns((rs) => rs?.map((r) => (r.run_id === after.run_id ? after : r)) ?? rs);
      if (after.status === 'cancelled' && after.started_at_ms == null) onCancelled?.(after.run_id);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not cancel the run');
    }
  }

  return (
    <div className="border-b border-slate-200/80 bg-slate-50/70 px-6 py-3" data-testid="run-history">
      <div className="mx-auto max-w-3xl">
        {error ? <p className="text-sm text-red-600">{error}</p> : null}
        {runs === null && !error ? (
          <p className="flex items-center gap-2 text-sm text-slate-500"><CircleNotch size={14} className="animate-spin" /> Loading runs…</p>
        ) : null}
        {runs?.length === 0 ? <p className="text-sm text-slate-500">No runs yet: each message you send starts one.</p> : null}
        {runs && runs.length > 0 ? (
          <ul className="max-h-60 divide-y divide-slate-200 overflow-auto text-xs">
            {runs.map((r) => {
              const tokens = r.usage.total_tokens ?? 0;
              return (
                <li key={r.run_id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2" data-testid="run-row" data-status={r.status} data-run-id={r.run_id}>
                  <span className={cn('rounded-full px-2 py-0.5 font-medium', TONE[r.status])}>{RUN_LABEL[r.status]}</span>
                  <span className="text-slate-500" title={new Date(r.created_at_ms).toLocaleString()}>
                    {new Date(r.created_at_ms).toLocaleTimeString()}
                  </span>
                  {runDuration(r) ? <span className="text-slate-500">{runDuration(r)}</span> : null}
                  {r.steps ? <span className="text-slate-500">{r.steps} step{r.steps === 1 ? '' : 's'}</span> : null}
                  {r.tool_calls ? <span className="text-slate-500">{r.tool_calls} tool call{r.tool_calls === 1 ? '' : 's'}</span> : null}
                  {tokens ? <span className="text-slate-500">{tokens.toLocaleString()} tokens</span> : null}
                  {r.usage.cost ? <span className="text-slate-500">${r.usage.cost.toFixed(4)}</span> : null}
                  {r.error ? <span className="truncate text-red-600" title={r.error.message}>{r.error.message}</span> : null}
                  <span className="ml-auto font-mono text-[10px] text-slate-400" title="Run id">{r.run_id.slice(0, 8)}</span>
                  {!RUN_ENDED.has(r.status) ? (
                    <button type="button" onClick={() => void cancel(r)} className="inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 text-red-600 hover:bg-red-50" data-testid="run-cancel">
                      <X size={12} /> Cancel
                    </button>
                  ) : null}
                </li>
              );
            })}
          </ul>
        ) : null}
      </div>
    </div>
  );
}
