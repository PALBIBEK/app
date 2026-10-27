'use client';

/**
 * The live session's right rail: its sandboxes, what it has used against its limits, and
 * the exact versions it runs.
 */

import { useEffect } from 'react';

import { Card, Meter, Mono, StatusPill, type StatusKind } from '@/components/ui';
import { useSandboxes } from '@/lib/api/hooks/useOperate';
import type { SandboxItem } from '@/lib/api/types/operate.types';
import { sandboxLabel } from '@/lib/session/runs';

import { PinnedCard } from './PinnedCard';
import type { SandboxView } from '@/lib/session/wireEventReducer';
import { formatCount, formatMoney, formatRelativeTime } from '@/lib/utils';

const SANDBOX_KIND: Record<SandboxItem['state'], { kind: StatusKind; label: string }> = {
  running: { kind: 'running', label: 'Running' },
  stopped: { kind: 'idle', label: 'Stopped · idle' },
  lost: { kind: 'failed', label: 'Lost' },
  not_started: { kind: 'stopped', label: 'Not started' },
};

const CAUSE: Record<string, string> = {
  prewarm: 'started with the run',
  prewarm_resumed: 'restored as the run began',
  first_use: 'started for the first tool',
  resumed: 'restored after an idle stop',
  replaced: 'replaced after a loss',
  adopted: 'taken over after a restart',
};

function Section({ title, aside, children }: { title: string; aside?: React.ReactNode; children: React.ReactNode }): JSX.Element {
  return (
    <Card className="flex flex-col gap-3 px-[18px] py-4">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-sm font-semibold">{title}</h2>
        {aside ? <span className="text-[12.5px] text-ink-3">{aside}</span> : null}
      </div>
      {children}
    </Card>
  );
}

function SandboxRow({ box }: { box: SandboxItem }): JSX.Element {
  const s = SANDBOX_KIND[box.state];
  const since = box.since_ms ? formatRelativeTime(new Date(box.since_ms)) : '';
  return (
    <div className="flex flex-col gap-1.5 rounded-[10px] border border-line-soft p-3" data-testid="rail-sandbox" data-state={box.state}>
      <div className="flex items-center gap-2">
        <Mono className="text-[13px] font-medium text-ink">{box.hand}</Mono>
        <StatusPill kind={s.kind} label={s.label} className="text-[12.5px]" />
        <span className="flex-1" />
        {since ? <span className="text-xs text-ink-3">{since}</span> : null}
      </div>
      <div className="font-mono text-xs text-ink-2">
        {[box.provider, box.sandbox_id ? box.sandbox_id.slice(0, 18) : null].filter(Boolean).join(' · ') || 'no sandbox yet'}
      </div>
      {box.state === 'running' && box.cause ? <div className="text-xs text-ink-3">{CAUSE[box.cause] ?? box.cause}</div> : null}
      {box.state === 'lost' && box.reason ? <div className="text-xs text-ink">{box.reason}</div> : null}
      {box.last_checkpoint_ms ? (
        <div className="text-xs text-ink-3">
          Saved {formatRelativeTime(new Date(box.last_checkpoint_ms))}
          {box.checkpoint_files ? ` · ${box.checkpoint_files} file${box.checkpoint_files === 1 ? '' : 's'}` : ''}
        </div>
      ) : null}
      {box.processes.length ? (
        <div className="flex flex-wrap gap-1.5">
          {box.processes.map((p) => (
            <Mono key={p} chip className="text-xs">
              {p}
            </Mono>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function SessionRail({
  sessionId,
  live,
  usage,
  steps,
  maxSteps,
  maxCost,
  sessionCost,
  children,
}: {
  sessionId: string;
  /** The stream's view of the default sandbox: shown until the sandboxes list loads. */
  live: SandboxView | null;
  usage: { input_tokens: number; output_tokens: number; cost: number };
  steps: number;
  maxSteps?: number;
  maxCost?: number;
  sessionCost: number;
  children?: React.ReactNode;
}): JSX.Element {
  const { data: boxes, isLoading, refetch } = useSandboxes({ sessionId, poll: true });
  // The stream saw the sandbox start, stop or get lost: read its state now rather than at the next poll.
  useEffect(() => {
    if (live) void refetch();
  }, [live, refetch]);
  const cost = Math.max(usage.cost, sessionCost);
  return (
    <aside aria-label="Session resources" className="flex min-w-0 flex-col gap-4">
      <Section title="Sandboxes" aside={boxes?.length ? `${boxes.length} attached` : undefined}>
        {boxes && boxes.length ? (
          boxes.map((b) => <SandboxRow key={b.hand} box={b} />)
        ) : (
          <p className="text-[13px] text-ink-2">{isLoading ? 'Loading…' : sandboxLabel(live) ?? 'No sandbox yet: one starts with the first tool that needs it.'}</p>
        )}
      </Section>

      <Section title="Budget" aside={maxCost ? undefined : 'no spend cap'}>
        <div>
          <div className="flex items-baseline gap-1.5">
            <span className="tabular text-[26px] font-semibold tracking-[-0.02em]" data-testid="rail-cost">
              {formatMoney(cost)}
            </span>
            {maxCost ? <span className="text-[13px] text-ink-2">of {formatMoney(maxCost)} cap</span> : null}
          </div>
          {maxCost ? <Meter value={cost} max={maxCost} label="Spend against the cap" className="mt-2 h-1.5" /> : null}
        </div>
        <div className="flex flex-col gap-2.5 text-[13px]">
          <div>
            <div className="flex justify-between">
              <span>Steps this run</span>
              <span className="tabular font-mono text-[12.5px]">
                {steps}
                {maxSteps ? <span className="text-ink-3"> / {maxSteps}</span> : null}
              </span>
            </div>
            {maxSteps ? <Meter value={steps} max={maxSteps} label="Steps against the limit" className="mt-1.5" /> : null}
          </div>
          <div className="grid grid-cols-2 gap-2 border-t border-line-soft pt-2.5">
            <div>
              <div className="text-xs text-ink-3">Input tokens</div>
              <div className="tabular font-mono text-[13px]" title={usage.input_tokens.toLocaleString()}>
                {formatCount(usage.input_tokens)}
              </div>
            </div>
            <div>
              <div className="text-xs text-ink-3">Output tokens</div>
              <div className="tabular font-mono text-[13px]" title={usage.output_tokens.toLocaleString()}>
                {formatCount(usage.output_tokens)}
              </div>
            </div>
          </div>
        </div>
      </Section>

      <PinnedCard sessionId={sessionId} />

      {children}
    </aside>
  );
}
