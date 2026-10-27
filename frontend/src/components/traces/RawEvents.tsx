'use client';

/** The journal events a run's trace was built from, as recorded: for deep debugging. */

import { Card, ErrorNote, SkeletonRows } from '@/components/ui';
import { useRunEvents } from '@/lib/api/hooks/useTraces';
import { formatMs } from '@/lib/traces/format';

export function RawEvents({ sessionId, runId, startedMs }: { sessionId: string; runId: string; startedMs: number }): JSX.Element {
  const { data, isLoading, error, refetch } = useRunEvents(sessionId, runId, true);
  return (
    <Card aria-label="Raw journal events" data-testid="raw-events">
      {error ? <ErrorNote message="Could not load the run's events." onRetry={() => void refetch()} /> : null}
      {isLoading ? <div className="p-4"><SkeletonRows rows={6} /></div> : null}
      <ul className="divide-y divide-line-soft">
        {(data?.events ?? []).map((e) => (
          <li key={e.seq}>
            <details className="px-4 py-2">
              <summary className="flex cursor-pointer flex-wrap items-center gap-x-3 gap-y-1 text-[13px]">
                <span className="tabular w-10 font-mono text-xs text-ink-3">#{e.seq}</span>
                <span className="tabular w-16 font-mono text-xs text-ink-3">+{formatMs(e.ts_ms - startedMs)}</span>
                <span className="font-medium">{e.event_type}</span>
                {e.turn_id == null ? <span className="text-xs text-ink-3">session event</span> : null}
              </summary>
              <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap rounded-lg bg-canvas px-3 py-2 font-mono text-xs">
                {JSON.stringify(e.payload, null, 2)}
              </pre>
            </details>
          </li>
        ))}
      </ul>
    </Card>
  );
}
