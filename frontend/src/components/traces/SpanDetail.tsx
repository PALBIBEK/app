'use client';

/**
 * One span, opened: its full content drawn for its kind. A model call reads as its reply
 * and the tools it asked for; a tool as its change, arguments and result (a command's
 * output as a terminal); an approval as the decision; plus the facts and journal events
 * every span has.
 */

import { useEffect, useState } from 'react';

import { Card, ErrorNote, SkeletonRows } from '@/components/ui';
import { useTraceSpan } from '@/lib/api/hooks/useTraces';
import type { RunTrace, TraceSpan } from '@/lib/api/types/trace.types';
import { KIND, LIVE_STATUSES, formatMs } from '@/lib/traces/format';
import { cn, formatMoney } from '@/lib/utils';

import { DiffView, changeCounts, type FileChange } from './DiffView';

type Tab = 'reply' | 'change' | 'args' | 'result' | 'decision' | 'content' | 'facts' | 'events';

const TAB_LABEL: Record<Tab, string> = {
  reply: 'Reply',
  change: 'Change',
  args: 'Arguments',
  result: 'Result',
  decision: 'Decision',
  content: 'Content',
  facts: 'Facts',
  events: 'Events',
};

function tabsFor(span: TraceSpan): Tab[] {
  const d = span.detail ?? {};
  switch (span.kind) {
    case 'llm':
      return ['reply', 'facts', 'events'];
    case 'tool':
      return [...(d.change ? (['change'] as Tab[]) : []), 'args', 'result', 'facts', 'events'];
    case 'approval':
      return ['decision', 'facts', 'events'];
    case 'step':
      return ['facts', 'events'];
    default:
      return ['content', 'facts', 'events'];
  }
}

function json(value: unknown): string {
  try {
    return typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function Pre({ children, dark }: { children: React.ReactNode; dark?: boolean }): JSX.Element {
  return (
    <pre
      className={cn(
        'max-h-[480px] overflow-auto whitespace-pre-wrap rounded-lg px-3 py-2.5 font-mono text-xs leading-relaxed',
        dark ? 'bg-slate-900 text-slate-100' : 'bg-canvas text-ink',
      )}
    >
      {children}
    </pre>
  );
}

function Label({ children }: { children: React.ReactNode }): JSX.Element {
  return <div className="text-xs text-ink-3">{children}</div>;
}

function Reply({ detail }: { detail: Record<string, unknown> }): JSX.Element {
  const calls = (detail.tool_calls as { id: string; name: string; arguments: unknown }[] | undefined) ?? [];
  const text = String(detail.text ?? '');
  return (
    <div className="flex flex-col gap-3">
      {text ? <p className="whitespace-pre-wrap text-[13.5px]">{text}</p> : <p className="text-[13px] text-ink-3">No text: it only asked for tools.</p>}
      {calls.length ? (
        <div className="flex flex-col gap-2">
          <Label>Asked for</Label>
          {calls.map((c) => (
            <div key={c.id} className="flex flex-col gap-1">
              <code className="font-mono text-[12.5px] font-medium">{c.name}</code>
              <Pre>{json(c.arguments)}</Pre>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function Result({ span }: { span: TraceSpan }): JSX.Element {
  const result = (span.detail?.result ?? {}) as { ok?: boolean; payload?: unknown; error?: string; code?: string };
  if (span.attrs.denied) return <p className="text-[13.5px] text-ink-2">Not run: {result.error ?? 'denied'}.</p>;
  if (result.ok === false) {
    return (
      <div className="flex flex-col gap-2">
        <p className="text-[13.5px] text-bad">{result.error ?? 'failed'}</p>
        {result.code ? <code className="font-mono text-xs text-ink-3">{result.code}</code> : null}
      </div>
    );
  }
  const p = result.payload as { exit_code?: number; stdout?: string; stderr?: string } | unknown;
  if (p && typeof p === 'object' && 'exit_code' in (p as object)) {
    const t = p as { exit_code?: number; stdout?: string; stderr?: string };
    return (
      <div className="flex flex-col gap-2">
        <Label>exit {String(t.exit_code)}</Label>
        {t.stdout ? <Pre dark>{t.stdout}</Pre> : null}
        {t.stderr ? (
          <>
            <Label>stderr</Label>
            <Pre dark>{t.stderr}</Pre>
          </>
        ) : null}
        {!t.stdout && !t.stderr ? <p className="text-[13px] text-ink-3">No output.</p> : null}
      </div>
    );
  }
  return result.payload == null ? <p className="text-[13px] text-ink-3">No result.</p> : <Pre>{json(result.payload)}</Pre>;
}

function Decision({ span }: { span: TraceSpan }): JSX.Element {
  const d = span.detail ?? {};
  const a = span.attrs;
  return (
    <dl className="grid grid-cols-[110px_minmax(0,1fr)] gap-x-3 gap-y-2 text-[13px]">
      <dt className="text-ink-3">Asked</dt>
      <dd>{String(d.prompt ?? '')}</dd>
      <dt className="text-ink-3">Choices</dt>
      <dd>{((d.options as string[] | undefined) ?? []).join(', ')}</dd>
      <dt className="text-ink-3">Answer</dt>
      <dd className={cn(a.denied ? 'text-bad' : 'text-good', 'font-medium')}>
        {a.decision ? String(a.decision) : 'waiting'}
        {a.outcome === 'timed_out' ? ' (no answer in time)' : ''}
      </dd>
      {a.responded_by ? (
        <>
          <dt className="text-ink-3">By</dt>
          <dd>{String(a.responded_by)}</dd>
        </>
      ) : null}
      <dt className="text-ink-3">Waited</dt>
      <dd>{formatMs(span.duration_ms)} of {formatMs(Number(a.timeout_s ?? 0) * 1000)} allowed</dd>
    </dl>
  );
}

function Content({ span }: { span: TraceSpan }): JSX.Element {
  const d = span.detail ?? {};
  const text = d.output ?? d.text ?? d.command;
  return (
    <div className="flex flex-col gap-3">
      {text ? <Pre>{String(text)}</Pre> : null}
      {Array.isArray(d.shown) && d.shown.length ? (
        <>
          <Label>Shown to the model</Label>
          <Pre>{json(d.shown)}</Pre>
        </>
      ) : null}
      {!text && !(Array.isArray(d.shown) && d.shown.length) ? <p className="text-[13px] text-ink-3">Nothing more to show.</p> : null}
    </div>
  );
}

const HIDDEN_FACTS = new Set(['exec_start_ms', 'call_id', 'index']);

function factValue(key: string, value: unknown): string {
  if (key === 'cost') return formatMoney(Number(value));
  if (key.endsWith('_ms')) return formatMs(Number(value));
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  return json(value);
}

function Facts({ span, trace }: { span: TraceSpan; trace: RunTrace }): JSX.Element {
  const facts = Object.entries(span.attrs).filter(([k, v]) => !HIDDEN_FACTS.has(k) && v !== null && v !== undefined);
  return (
    <dl className="grid grid-cols-[140px_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-[13px]">
      <dt className="text-ink-3">started at</dt>
      <dd className="tabular">{formatMs(span.start_ms - trace.rollup.started_ms)} into the run</dd>
      {facts.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-ink-3">{k.replace(/_/g, ' ')}</dt>
          <dd className="tabular break-words font-mono text-[12.5px]">{factValue(k, v)}</dd>
        </div>
      ))}
      {span.markers.map((m) => (
        <div key={m.seq} className="contents">
          <dt className="text-ink-3">at {formatMs(m.ts_ms - trace.rollup.started_ms)}</dt>
          <dd>{m.text}</dd>
        </div>
      ))}
    </dl>
  );
}

export function SpanDetail({ trace, spanId }: { trace: RunTrace; spanId: string }): JSX.Element {
  const live = LIVE_STATUSES.includes(trace.rollup.status);
  const { data, isLoading, error, refetch } = useTraceSpan(trace.session_id, trace.run_id, spanId, live);
  const base = trace.spans.find((s) => s.id === spanId);
  const span = data ?? base;
  const tabs = span ? tabsFor(data ?? { ...span, detail: {} }) : [];
  const [tab, setTab] = useState<Tab | null>(null);
  useEffect(() => setTab(null), [spanId]);
  const current = tab && tabs.includes(tab) ? tab : tabs[0];

  if (!span) return <Card padded>That span is not part of this run.</Card>;
  const k = KIND[span.kind] ?? KIND.job;
  const change = data?.detail?.change as FileChange | undefined;
  const counts = change ? changeCounts(change) : null;

  return (
    <Card as="aside" aria-label="Selected span" className="flex min-w-0 flex-col" data-testid="span-detail" data-kind={span.kind}>
      <div className="flex flex-col gap-1.5 border-b border-line-soft px-4 py-3.5">
        <div className="flex min-w-0 items-center gap-2">
          <span className={cn('flex-none rounded-[5px] px-1.5 py-px text-[10.5px] font-semibold uppercase tracking-[0.04em]', k.chip)}>
            {k.label}
          </span>
          <h2 className="min-w-0 truncate font-mono text-[14.5px] font-semibold">{span.kind === 'tool' ? span.preview ?? span.name : span.kind === 'agent' && !span.parent_id ? trace.agent_name ?? span.name : span.name}</h2>
        </div>
        <div className="text-[12.5px] text-ink-3">
          {span.duration_ms != null ? formatMs(span.duration_ms) : 'still going'}
          {span.kind === 'tool' && span.attrs.exec_ms != null && span.duration_ms !== span.attrs.exec_ms
            ? ` · ran ${formatMs(Number(span.attrs.exec_ms))}`
            : ''}
          {counts ? ` · +${counts.added} −${counts.removed}` : ''}
          {' · '}
          <span className={span.status === 'error' ? 'text-bad' : undefined}>{span.attrs.denied ? 'not run' : span.status}</span>
        </div>
        {span.error ? (
          <p className="rounded-lg bg-red-50 px-3 py-2 text-[13px] text-red-900">
            <span className="font-semibold">{span.error.class}</span>: {span.error.message}
          </p>
        ) : null}
      </div>
      <div role="tablist" aria-label="Span detail" className="flex gap-0.5 overflow-x-auto border-b border-line-soft px-3 pt-1.5">
        {tabs.map((t) => (
          <button
            key={t}
            type="button"
            role="tab"
            aria-selected={t === current}
            onClick={() => setTab(t)}
            className={cn(
              '-mb-px whitespace-nowrap border-b-2 px-2.5 py-2 text-[13px]',
              t === current ? 'border-accent-500 font-semibold text-ink' : 'border-transparent text-ink-2 hover:text-ink',
            )}
          >
            {TAB_LABEL[t]}
            {t === 'events' ? ` ${span.seqs.length}` : ''}
          </button>
        ))}
      </div>
      <div className="flex flex-col gap-3 px-4 py-3.5" role="tabpanel">
        {error ? <ErrorNote message="Could not load this span." onRetry={() => void refetch()} /> : null}
        {isLoading && !data ? <SkeletonRows rows={3} /> : null}
        {data ? (
          <>
            {current === 'reply' ? <Reply detail={data.detail ?? {}} /> : null}
            {current === 'change' && change ? <DiffView change={change} /> : null}
            {current === 'args' ? <Pre>{json(data.detail?.args ?? {})}</Pre> : null}
            {current === 'result' ? <Result span={data} /> : null}
            {current === 'decision' ? <Decision span={data} /> : null}
            {current === 'content' ? <Content span={data} /> : null}
          </>
        ) : null}
        {current === 'facts' ? <Facts span={span} trace={trace} /> : null}
        {current === 'events' ? (
          <p className="text-[13px] text-ink-2">
            Built from journal events{' '}
            <span className="tabular font-mono text-[12.5px]">{span.seqs.join(', ')}</span>. The Raw events tab shows them as recorded.
          </p>
        ) : null}
      </div>
    </Card>
  );
}
