'use client';

/**
 * A run read as a conversation (``buildTranscript``): the ask, what the agent said, each
 * thing it did with its outcome, and how it ended. A card's "Details" opens the span in the
 * tree view; a file change loads its diff when it scrolls into the transcript.
 */

import { useMemo } from 'react';

import { Card } from '@/components/ui';
import { useTraceSpan } from '@/lib/api/hooks/useTraces';
import type { RunTrace, TraceSpan } from '@/lib/api/types/trace.types';
import { formatMs, runStatus } from '@/lib/traces/format';
import { buildTranscript, type TranscriptItem } from '@/lib/traces/transcript';
import { cn, formatMoney } from '@/lib/utils';

import { DiffView, type FileChange } from './DiffView';

type Open = (spanId: string) => void;

function Chip({ children, className }: { children: React.ReactNode; className?: string }): JSX.Element {
  return (
    <span className={cn('rounded-[5px] px-1.5 py-px text-[10.5px] font-semibold uppercase tracking-[0.04em]', className)}>
      {children}
    </span>
  );
}

function DetailsButton({ id, onOpen }: { id: string; onOpen: Open }): JSX.Element {
  return (
    <button type="button" onClick={() => onOpen(id)} className="text-xs text-ink-3 hover:text-ink hover:underline">
      Details
    </button>
  );
}

function Divider({ children }: { children: React.ReactNode }): JSX.Element {
  return (
    <div className="flex items-center gap-2.5 text-xs text-ink-3">
      <span className="flex-none">{children}</span>
      <span aria-hidden="true" className="h-px flex-1 bg-line" />
    </div>
  );
}

function exitTone(code: unknown): string {
  return code === 0 ? 'bg-emerald-50 text-emerald-800' : 'bg-amber-50 text-amber-900';
}

/** A file change's diff, read from the span's detail (the tree carries no file contents). */
function ChangeBlock({ trace, span }: { trace: RunTrace; span: TraceSpan }): JSX.Element | null {
  const live = trace.rollup.status === 'running' || trace.rollup.status === 'awaiting_approval';
  const { data } = useTraceSpan(trace.session_id, trace.run_id, span.id, live);
  const change = data?.detail?.change as FileChange | undefined;
  if (!change) return null;
  return (
    <div className="border-t border-line-soft p-3">
      <DiffView change={change} maxLines={60} />
    </div>
  );
}

/** "allowed by you after 2.2 s": the preview, with who answered when the product knows. */
export function approvalLine(approval: TraceSpan): string {
  const a = approval.attrs;
  if (a.decision == null) return 'waiting for a person';
  if (a.outcome === 'timed_out' || a.outcome === 'cancelled') return approval.preview ?? String(a.outcome);
  const who = a.responded_by === 'you' ? ' by you' : '';
  return `${a.denied ? 'denied' : 'allowed'}${who} after ${formatMs(approval.duration_ms)}`;
}

function ToolCard({
  trace,
  span,
  approval,
  onOpen,
}: {
  trace: RunTrace;
  span: TraceSpan;
  approval: TraceSpan | null;
  onOpen: Open;
}): JSX.Element {
  const tool = String(span.attrs.tool ?? span.name);
  const denied = Boolean(span.attrs.denied);
  const failed = span.status === 'error';
  const exit = span.attrs.exit_code;
  const changes = tool === 'edit' || tool === 'write';
  return (
    <Card
      as="article"
      className={cn(failed && 'border-red-200')}
      data-testid="transcript-tool"
      data-tool={tool}
      data-status={denied ? 'denied' : span.status}
    >
      <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 px-4 py-3">
        <Chip className="bg-teal-50 text-teal-800">{tool}</Chip>
        <code className="min-w-0 truncate font-mono text-[13px]">{span.preview}</code>
        {exit !== undefined && !denied ? (
          <span className={cn('rounded-full px-2 py-px text-xs', exitTone(exit))}>exit {String(exit)}</span>
        ) : null}
        {failed ? <span className="rounded-full bg-red-50 px-2 py-px text-xs text-red-800">failed</span> : null}
        {denied ? <span className="rounded-full bg-slate-100 px-2 py-px text-xs text-slate-700">not run</span> : null}
        <span className="flex-1" />
        {span.attrs.exec_ms != null && !denied ? (
          <span className="tabular font-mono text-xs text-ink-3">ran {formatMs(Number(span.attrs.exec_ms))}</span>
        ) : null}
        <DetailsButton id={span.id} onOpen={onOpen} />
      </div>
      {approval ? (
        <div
          className={cn(
            'border-y px-4 py-2 text-[12.5px]',
            approval.attrs.denied ? 'border-slate-200 bg-slate-50 text-slate-700' : 'border-amber-100 bg-amber-50/70 text-amber-900',
          )}
          data-testid="transcript-approval"
        >
          {approvalLine(approval)}
        </div>
      ) : null}
      {changes && !denied && !failed ? <ChangeBlock trace={trace} span={span} /> : null}
      {!changes && span.excerpt && !denied ? (
        tool === 'bash' || exit !== undefined ? (
          <pre className="max-h-72 overflow-auto whitespace-pre-wrap bg-slate-900 px-4 py-3 font-mono text-xs leading-relaxed text-slate-100">
            {span.excerpt}
          </pre>
        ) : (
          <pre className="max-h-48 overflow-auto whitespace-pre-wrap border-t border-line-soft bg-canvas px-4 py-3 font-mono text-xs leading-relaxed text-ink-2">
            {span.excerpt}
          </pre>
        )
      ) : null}
    </Card>
  );
}

function ExploreCard({ spans, onOpen }: { spans: TraceSpan[]; onOpen: Open }): JSX.Element {
  const ms = spans.reduce((sum, s) => sum + Number(s.attrs.exec_ms ?? s.duration_ms ?? 0), 0);
  return (
    <details className="rounded-card border border-line bg-surface px-4 py-3" data-testid="transcript-explore">
      <summary className="flex cursor-pointer flex-wrap items-center gap-2.5 text-[13.5px]">
        <Chip className="bg-teal-50 text-teal-800">Tools</Chip>
        <span className="font-medium">Looked at {spans.length} things</span>
        <span className="min-w-0 truncate text-ink-3">{spans.map((s) => s.preview).join(' · ')}</span>
        <span className="tabular ml-auto font-mono text-xs text-ink-3">{formatMs(ms)}</span>
      </summary>
      <ul className="mt-2 flex flex-col gap-1.5 border-t border-line-soft pt-2">
        {spans.map((s) => (
          <li key={s.id} className="flex flex-wrap items-center gap-2 text-[13px]">
            <code className="font-mono">{s.preview}</code>
            {s.attrs.outcome ? <span className="text-ink-3">{String(s.attrs.outcome)}</span> : null}
            <span className="flex-1" />
            <DetailsButton id={s.id} onOpen={onOpen} />
          </li>
        ))}
      </ul>
    </details>
  );
}

function OtherCard({ span, onOpen }: { span: TraceSpan; onOpen: Open }): JSX.Element {
  const label = { memory: 'Memory', compaction: 'Compacted', agent: 'Subagent', job: 'Job' }[span.kind as string] ?? span.kind;
  return (
    <Card as="article" className="flex flex-col gap-1.5 px-4 py-3" data-testid={`transcript-${span.kind}`}>
      <div className="flex flex-wrap items-center gap-2.5 text-[13.5px]">
        <Chip className="bg-violet-50 text-violet-800">{label}</Chip>
        <span className="font-medium">{span.name}</span>
        <span className="min-w-0 truncate text-ink-3">{span.preview}</span>
        <span className="flex-1" />
        <DetailsButton id={span.id} onOpen={onOpen} />
      </div>
      {span.excerpt && span.kind !== 'memory' ? <p className="whitespace-pre-wrap text-[13.5px] text-ink-2">{span.excerpt}</p> : null}
    </Card>
  );
}

function Item({ trace, item, onOpen }: { trace: RunTrace; item: TranscriptItem; onOpen: Open }): JSX.Element | null {
  switch (item.type) {
    case 'input':
      return (
        <Card as="article" className="flex flex-col gap-1.5 px-4 py-3" data-testid="transcript-input">
          <div className="text-[12.5px] text-ink-3">
            <span className="font-semibold text-ink">You</span>
          </div>
          <p className="whitespace-pre-wrap">{item.text}</p>
        </Card>
      );
    case 'step':
      return (
        <Divider>
          Step {item.index}
          {item.durationMs != null ? ` · ${formatMs(item.durationMs)}` : ' · going'}
          {item.humanMs > 0 ? `, ${formatMs(item.humanMs)} of it waiting on you` : ''}
        </Divider>
      );
    case 'say': {
      const s = item.span;
      return (
        <Card
          as="article"
          className={cn('flex flex-col gap-1.5 px-4 py-3', item.final && 'border-accent-200')}
          data-testid={item.final ? 'transcript-answer' : 'transcript-say'}
        >
          <div className="flex flex-wrap items-center gap-2 text-[12.5px] text-ink-3">
            <span className="font-semibold text-accent-700">Agent</span>
            {item.final ? <span>· answer</span> : null}
            <span className="flex-1" />
            <span className="tabular font-mono text-xs">
              {formatMs(s.duration_ms)}
              {s.attrs.cost ? ` · ${formatMoney(Number(s.attrs.cost))}` : ''}
            </span>
            <DetailsButton id={s.id} onOpen={onOpen} />
          </div>
          <p className="whitespace-pre-wrap">{s.excerpt}</p>
        </Card>
      );
    }
    case 'tool':
      return <ToolCard trace={trace} span={item.span} approval={item.approval} onOpen={onOpen} />;
    case 'explore':
      return <ExploreCard spans={item.spans} onOpen={onOpen} />;
    case 'other':
      return <OtherCard span={item.span} onOpen={onOpen} />;
    case 'marker':
      return <div className="pl-1 text-xs text-ink-3">{item.marker.text}</div>;
    case 'end': {
      const st = runStatus(item.status);
      return (
        <div
          className={cn(
            'rounded-card border px-4 py-3 text-[13.5px]',
            st.kind === 'failed' ? 'border-red-200 bg-red-50 text-red-900' : 'border-line bg-surface text-ink-2',
          )}
          data-testid="transcript-end"
        >
          <span className="font-semibold">{st.label}</span>
          {item.reason ? `: ${item.reason}` : ''}
        </div>
      );
    }
  }
}

export function Transcript({ trace, onOpen }: { trace: RunTrace; onOpen: Open }): JSX.Element {
  const items = useMemo(() => buildTranscript(trace), [trace]);
  return (
    <section aria-label="Transcript" className="flex min-w-0 flex-col gap-3" data-testid="transcript">
      {items.map((item) => (
        <Item key={item.key} trace={trace} item={item} onOpen={onOpen} />
      ))}
      {trace.rollup.status === 'running' || trace.rollup.status === 'awaiting_approval' ? (
        <p className="text-sm text-ink-3">{runStatus(trace.rollup.status).label}: this updates as the run goes.</p>
      ) : null}
    </section>
  );
}
