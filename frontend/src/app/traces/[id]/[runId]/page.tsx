'use client';

/**
 * One run's trace. Transcript (the default) reads it as a conversation; Tree shows every
 * span on a timeline with the selected one opened beside it; Raw events shows the journal
 * as recorded. A run still going refreshes itself.
 */

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useEffect, useMemo, useState } from 'react';

import { AppShell, PageBody } from '@/components/layout/AppShell';
import { RawEvents } from '@/components/traces/RawEvents';
import { RunHeader } from '@/components/traces/RunHeader';
import { ChecksCard, FeedbackButtons } from '@/components/traces/Scores';
import { SpanDetail } from '@/components/traces/SpanDetail';
import { SpanTree } from '@/components/traces/SpanTree';
import { Transcript } from '@/components/traces/Transcript';
import { Card, ErrorNote, SkeletonRows } from '@/components/ui';
import { useRunTrace, useSessionTraces } from '@/lib/api/hooks/useTraces';
import type { RunTrace } from '@/lib/api/types/trace.types';
import { LIVE_STATUSES, formatMs } from '@/lib/traces/format';
import { cn } from '@/lib/utils';

type Tab = 'transcript' | 'tree' | 'raw';
const TABS: { value: Tab; label: string }[] = [
  { value: 'transcript', label: 'Transcript' },
  { value: 'tree', label: 'Tree' },
  { value: 'raw', label: 'Raw events' },
];

function useNow(ticking: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!ticking) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [ticking]);
  return now;
}

/** The span a tree opens on: the first failure, else the slowest call, else the run. */
function firstFocus(trace: RunTrace): string | null {
  return (
    trace.spans.find((s) => s.status === 'error' && s.kind !== 'agent')?.id ??
    trace.rollup.slowest_span_id ??
    trace.spans[0]?.id ??
    null
  );
}

function Touched({ trace }: { trace: RunTrace }): JSX.Element | null {
  const tools = trace.spans.filter((s) => s.kind === 'tool' && !s.attrs.denied);
  const of = (names: string[]): string[] =>
    Array.from(new Set(tools.filter((s) => names.includes(String(s.attrs.tool))).map((s) => String(s.preview ?? s.name))));
  const read = of(['read', 'ls', 'grep', 'glob']);
  const changed = of(['edit', 'write']);
  const ran = of(['bash']);
  const markers = trace.spans.flatMap((s) => s.markers);
  const rows: [string, string[]][] = [
    ['Looked at', read],
    ['Changed', changed],
    ['Ran', ran],
    ['Models', trace.rollup.models],
  ];
  if (!rows.some(([, v]) => v.length) && !markers.length) return null;
  return (
    <Card className="flex flex-col gap-2.5 px-4 py-3.5" aria-label="What it touched">
      <h2 className="text-sm font-semibold">What it touched</h2>
      <dl className="grid grid-cols-[84px_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-[13px]">
        {rows
          .filter(([, v]) => v.length)
          .map(([label, v]) => (
            <div key={label} className="contents">
              <dt className="text-ink-3">{label}</dt>
              <dd className="flex min-w-0 flex-col gap-0.5">
                {v.slice(0, 8).map((x) => (
                  <code key={x} className="truncate font-mono text-[12.5px]">
                    {x}
                  </code>
                ))}
                {v.length > 8 ? <span className="text-xs text-ink-3">and {v.length - 8} more</span> : null}
              </dd>
            </div>
          ))}
        {markers.length ? (
          <>
            <dt className="text-ink-3">Sandbox</dt>
            <dd className="flex flex-col gap-0.5">
              {markers.map((m) => (
                <span key={m.seq}>
                  {m.text} <span className="text-ink-3">at {formatMs(m.ts_ms - trace.rollup.started_ms)}</span>
                </span>
              ))}
            </dd>
          </>
        ) : null}
      </dl>
    </Card>
  );
}

function SessionRuns({ sessionId, runId }: { sessionId: string; runId: string }): JSX.Element | null {
  const { data } = useSessionTraces(sessionId);
  if (!data || data.items.length < 2) return null;
  return (
    <Card className="flex flex-col gap-2 px-4 py-3.5" aria-label="This session's runs">
      <h2 className="text-sm font-semibold">This session</h2>
      <ol className="flex flex-col gap-1 text-[13px]">
        {data.items.map((r, i) => (
          <li key={r.run_id} className="flex gap-2">
            <span className="tabular w-5 text-right text-ink-3">{i + 1}</span>
            {r.run_id === runId ? (
              <span className="min-w-0 flex-1 truncate font-semibold">{r.input || 'Run'}</span>
            ) : (
              <Link href={`/traces/${sessionId}/${r.run_id}`} className="min-w-0 flex-1 truncate text-accent-600 hover:underline">
                {r.input || 'Run'}
              </Link>
            )}
            <span className="tabular text-ink-3">{r.duration_ms != null ? formatMs(r.duration_ms) : 'going'}</span>
          </li>
        ))}
      </ol>
    </Card>
  );
}

export default function RunTracePage(): JSX.Element {
  const params = useParams();
  const sessionId = typeof params.id === 'string' ? params.id : '';
  const runId = typeof params.runId === 'string' ? params.runId : '';
  const { data: trace, isLoading, error, refetch } = useRunTrace(sessionId, runId);
  const [tab, setTab] = useState<Tab>('transcript');
  const [picked, setPicked] = useState<string | null>(null);
  const live = !!trace && LIVE_STATUSES.includes(trace.rollup.status);
  const now = useNow(live);
  const selected = useMemo(() => (trace ? picked ?? firstFocus(trace) : null), [trace, picked]);

  const open = (spanId: string): void => {
    setPicked(spanId);
    setTab('tree');
  };

  return (
    <AppShell>
      <PageBody>
        <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-2 text-sm text-ink-3">
          <Link href="/traces" className="hover:text-ink">
            Traces
          </Link>
          <span aria-hidden="true">/</span>
          <Link href={`/traces/${sessionId}`} className="min-w-0 truncate hover:text-ink">
            {trace?.session_title || 'Session'}
          </Link>
          <span aria-hidden="true">/</span>
          <span className="text-ink">Run</span>
        </nav>

        {error ? <ErrorNote message="Could not load this run's trace." onRetry={() => void refetch()} /> : null}
        {isLoading ? (
          <Card padded>
            <SkeletonRows rows={5} />
          </Card>
        ) : null}

        {trace ? (
          <>
            <RunHeader trace={trace} />
            <div role="tablist" aria-label="Views of this run" className="flex gap-1 border-b border-line">
              {TABS.map((t) => (
                <button
                  key={t.value}
                  type="button"
                  role="tab"
                  aria-selected={tab === t.value}
                  onClick={() => setTab(t.value)}
                  className={cn(
                    '-mb-px border-b-2 px-3.5 py-2.5 text-[13.5px]',
                    tab === t.value ? 'border-accent-500 font-semibold text-ink' : 'border-transparent text-ink-2 hover:text-ink',
                  )}
                >
                  {t.label}
                </button>
              ))}
            </div>

            {tab === 'transcript' ? (
              <div className="flex flex-wrap items-start gap-4">
                <div className="min-w-0 flex-[999_1_640px]">
                  <Transcript trace={trace} onOpen={open} />
                </div>
                <div className="flex min-w-0 flex-[1_1_320px] flex-col gap-4">
                  {LIVE_STATUSES.includes(trace.rollup.status) ? null : (
                    <Card className="flex flex-col gap-2.5 px-4 py-3.5" aria-label="Your verdict">
                      <h2 className="text-sm font-semibold">How did it go?</h2>
                      <FeedbackButtons trace={trace} noun="run" />
                    </Card>
                  )}
                  <ChecksCard trace={trace} onOpen={open} />
                  <Touched trace={trace} />
                  <SessionRuns sessionId={sessionId} runId={runId} />
                </div>
              </div>
            ) : null}

            {tab === 'tree' ? (
              <div className="flex flex-wrap items-start gap-4">
                <div className="min-w-0 flex-[999_1_640px]">
                  <SpanTree trace={trace} selectedId={selected} onSelect={setPicked} now={now} />
                </div>
                <div className="min-w-0 flex-[1_1_380px] xl:sticky xl:top-4">
                  {selected ? <SpanDetail trace={trace} spanId={selected} /> : null}
                </div>
              </div>
            ) : null}

            {tab === 'raw' ? <RawEvents sessionId={sessionId} runId={runId} startedMs={trace.rollup.started_ms} /> : null}
          </>
        ) : null}
      </PageBody>
    </AppShell>
  );
}
