'use client';

/**
 * The top of a run's page: how it ended and why, what was asked, which agent version ran it,
 * the numbers that matter, and where the time went.
 */

import Link from 'next/link';

import { Card, Mono, StatusPill, shortHash } from '@/components/ui';
import type { RunTrace } from '@/lib/api/types/trace.types';
import { formatMs, runStatus, toolsLine } from '@/lib/traces/format';
import { formatCount, formatMoney, formatStamp } from '@/lib/utils';

import { SaveToDataset } from './SaveToDataset';
import { TimeSplitBar } from './TimeSplitBar';

function Fact({ label, children }: { label: string; children: React.ReactNode }): JSX.Element {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-ink-3">{label}</dt>
      <dd className="tabular mt-0.5 truncate text-lg font-semibold">{children}</dd>
    </div>
  );
}

export function RunHeader({ trace }: { trace: RunTrace }): JSX.Element {
  const r = trace.rollup;
  const status = runStatus(r.status);
  const denied = r.denials;
  return (
    <Card className="flex flex-col gap-4 px-5 py-[18px] sm:px-6" aria-label="Run summary" data-testid="run-header">
      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[12.5px] text-ink-3">
          <StatusPill kind={status.kind} label={status.label} />
          {trace.agent_name ? (
            <span>
              {trace.agent_id ? (
                <Link href={`/harness/${trace.agent_id}`} className="text-ink-2 hover:text-ink">
                  {trace.agent_name}
                </Link>
              ) : (
                trace.agent_name
              )}
            </span>
          ) : null}
          {trace.version != null ? (
            <Mono className="rounded-md bg-accent-50 px-1.5 text-accent-700" title={trace.composition_digest ?? undefined}>
              v{trace.version}
              {trace.composition_digest ? ` · ${shortHash(trace.composition_digest)}` : ''}
            </Mono>
          ) : null}
          <span>started {formatStamp(r.started_ms)}</span>
          <Link href={`/sessions/${trace.session_id}`} className="text-accent-600 hover:underline">
            Open session
          </Link>
        </div>
        <h1 className="text-xl font-semibold tracking-[-0.01em]" data-testid="run-input">
          {r.input || 'Run'}
        </h1>
        {trace.summary ? (
          <p className="text-ink-2" data-testid="run-summary">
            {trace.summary}
          </p>
        ) : null}
        {r.reason && !trace.summary?.includes(r.reason) ? (
          <p className={status.kind === 'failed' ? 'text-bad' : 'text-ink-2'} data-testid="run-reason">
            {r.reason}
          </p>
        ) : null}
      </div>

      <dl className="grid grid-cols-[repeat(auto-fit,minmax(140px,1fr))] gap-4 border-t border-line-soft pt-4">
        <Fact label="Duration">{r.duration_ms != null ? formatMs(r.duration_ms) : 'still going'}</Fact>
        <Fact label="Cost">{formatMoney(r.cost)}</Fact>
        <Fact label="Tokens in / out">
          {formatCount(r.input_tokens)} / {formatCount(r.output_tokens)}
        </Fact>
        <Fact label="Steps">{r.steps}</Fact>
        <Fact label="Tool calls">
          {r.tool_calls}
          {r.tool_calls ? <span className="ml-1.5 text-[12.5px] font-normal text-ink-3">{toolsLine(r.tools)}</span> : null}
        </Fact>
        <Fact label="Approvals">
          {r.approvals}
          {r.approvals ? (
            <span className="ml-1.5 text-[12.5px] font-normal text-ink-3">
              {denied ? `${denied} denied` : 'all allowed'}
            </span>
          ) : null}
        </Fact>
      </dl>

      {r.duration_ms != null ? (
        <div className="flex flex-col gap-2">
          <div className="text-xs text-ink-3">Where the {formatMs(r.duration_ms)} went</div>
          <TimeSplitBar time={r.time} />
        </div>
      ) : null}
      {r.ended_ms != null ? (
        <div className="flex flex-wrap gap-2 border-t border-line-soft pt-3">
          <SaveToDataset trace={trace} />
        </div>
      ) : null}
    </Card>
  );
}
