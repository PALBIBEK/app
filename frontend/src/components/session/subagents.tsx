'use client';

/**
 * Subagents in a conversation: the call that started one (its task, and its answer once it
 * gave one), what one told the agent later (it finished, or sent an update), and the rail's
 * list of every subagent below the session.
 */

import { useState } from 'react';
import Link from 'next/link';
import { ArrowSquareOut, CaretDown, CaretRight, CircleNotch, GitBranch, Stop } from '@phosphor-icons/react';
import { toast } from 'sonner';

import { Card, StatusPill, type StatusKind } from '@/components/ui';
import { sessionsAPI } from '@/lib/api/endpoints/sessions';
import type { ChatMessage, Subagent, ToolCallView } from '@/lib/api/types/session.types';
import {
  kindLabel,
  noticeHeadline,
  statusLabel,
  subagentCall,
  subagentName,
  treeOrder,
  type SubagentStatus,
} from '@/lib/session/subagents';
import { cn, formatMoney } from '@/lib/utils';

/** Where a subagent's transcript is read. */
export function subagentHref(sessionId: string, subagentId: string): string {
  return `/sessions/${encodeURIComponent(sessionId)}/subagents/${encodeURIComponent(subagentId)}`;
}

export function statusKind(status: SubagentStatus): StatusKind {
  switch (status) {
    case 'starting':
    case 'running':
      return 'running';
    case 'ready':
      return 'idle';
    case 'completed':
      return 'done';
    case 'failed':
      return 'failed';
    case 'cancelled':
      return 'stopped';
  }
}

/** Long text, a few lines until opened. */
function Excerpt({ text, lines = 4, testId }: { text: string; lines?: number; testId?: string }): JSX.Element {
  const [open, setOpen] = useState(false);
  const long = text.length > 280 || text.split('\n').length > lines;
  return (
    <div className="flex flex-col items-start gap-1">
      <p data-testid={testId} className={cn('whitespace-pre-wrap break-words', !open && long && 'line-clamp-4')}>
        {text}
      </p>
      {long ? (
        <button type="button" onClick={() => setOpen((o) => !o)} className="text-[12.5px] font-medium text-accent-700 hover:text-accent-600">
          {open ? 'Show less' : 'Show all'}
        </button>
      ) : null}
    </div>
  );
}

const CALL_STATE: Record<'working' | 'started' | 'answered' | 'failed', { kind: StatusKind; label: string }> = {
  working: { kind: 'running', label: 'Working' },
  started: { kind: 'idle', label: 'Started' },
  answered: { kind: 'done', label: 'Answered' },
  failed: { kind: 'failed', label: 'Did not finish' },
};

/** A tool call that started a subagent: what it was asked, how it runs, and its answer. */
export function SubagentCallCard({
  tool,
  sessionId,
  startedId,
}: {
  tool: ToolCallView;
  /** The (product) session the conversation belongs to; links open its subagents. */
  sessionId: string;
  /** The subagent this call started, known from its start event while the call runs. */
  startedId?: string;
}): JSX.Element | null {
  const call = subagentCall(tool, startedId);
  if (!call) return null;
  const state = CALL_STATE[call.state];
  return (
    <div data-testid="subagent-card" data-state={call.state} className="overflow-hidden rounded-[10px] border border-line bg-surface text-sm">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
        <GitBranch size={15} className="shrink-0 text-ink-2" aria-hidden="true" />
        <span className="font-medium text-ink">
          Subagent{call.label ? <span className="text-ink-2"> · {call.label}</span> : null}
        </span>
        <span className="text-[12.5px] text-ink-3">
          {call.forked ? 'Continues this conversation · ' : ''}
          {kindLabel(call.kind)}
        </span>
        <span className="flex-1" />
        {call.state === 'working' ? (
          <span className="inline-flex items-center gap-1 text-xs text-ink-2">
            <CircleNotch size={13} className="animate-spin text-accent-600" /> Working
          </span>
        ) : (
          <StatusPill kind={state.kind} label={state.label} className="text-xs" />
        )}
      </div>
      {call.task ? (
        <div className="border-t border-line-soft px-3 py-2 text-[13px] text-ink-2">
          <div className="mb-0.5 text-xs font-medium uppercase tracking-wide text-ink-3">Task</div>
          <Excerpt text={call.task} lines={3} testId="subagent-task" />
        </div>
      ) : null}
      {call.answer ? (
        <div className={cn('border-t border-line-soft px-3 py-2 text-[13px]', call.state === 'failed' ? 'text-ink' : 'bg-canvas/60 text-ink')}>
          <div className="mb-0.5 text-xs font-medium uppercase tracking-wide text-ink-3">{call.state === 'failed' ? 'Why' : 'Answer'}</div>
          <Excerpt text={call.answer} testId="subagent-answer" />
        </div>
      ) : call.state === 'started' ? (
        <p className="border-t border-line-soft px-3 py-2 text-[13px] text-ink-2">
          {call.kind === 'continuable'
            ? 'It works on its first task now and stays for more. The agent is told each time it finishes.'
            : 'It works while the agent goes on. The agent is told when it finishes.'}
        </p>
      ) : null}
      {call.subagentId ? (
        <div className="border-t border-line-soft px-3 py-1.5">
          <Link
            href={subagentHref(sessionId, call.subagentId)}
            className="inline-flex items-center gap-1 text-[13px] font-medium text-accent-700 hover:text-accent-600"
          >
            {call.state === 'working' ? 'Watch it work' : 'Open its conversation'} <ArrowSquareOut size={13} aria-hidden="true" />
          </Link>
        </div>
      ) : null}
    </div>
  );
}

/** What a subagent told the agent after its call came back: it finished, or sent an update. */
export function SubagentNoticeLine({
  message,
  sessionId,
  name,
}: {
  message: ChatMessage;
  sessionId: string;
  /** Its label or task, when the listing has it. */
  name?: string;
}): JSX.Element | null {
  const notice = message.notice;
  if (!notice) return null;
  return (
    <div data-testid="subagent-notice" data-kind={notice.kind} className="ml-10 flex flex-col gap-1.5 rounded-[10px] border border-dashed border-line-strong px-3 py-2 text-[13px]">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-ink-2">
        <GitBranch size={14} aria-hidden="true" />
        <span>
          <span className="font-medium text-ink">{name ?? 'A subagent'}</span> {noticeHeadline(notice)}
        </span>
        <span className="flex-1" />
        <Link href={subagentHref(sessionId, notice.subagentId)} className="font-medium text-accent-700 hover:text-accent-600">
          Open
        </Link>
      </div>
      {notice.text ? (
        <div className="text-ink">
          <Excerpt text={notice.text} lines={3} />
        </div>
      ) : null}
    </div>
  );
}

function SubagentRow({ subagent, sessionId }: { subagent: Subagent; sessionId: string }): JSX.Element {
  const [stopping, setStopping] = useState(false);
  const working = subagent.status === 'running' || subagent.status === 'starting';
  async function stop(): Promise<void> {
    setStopping(true);
    try {
      await sessionsAPI.stopSubagent(sessionId, subagent.id);
      toast.success('Stopping it: the agent is told it was stopped');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not stop it');
    } finally {
      setStopping(false);
    }
  }
  return (
    <li
      data-testid="rail-subagent"
      data-status={subagent.status}
      className="flex flex-col gap-1 rounded-[10px] border border-line-soft p-3"
      style={{ marginLeft: `${Math.min(subagent.depth - 1, 4) * 14}px` }}
    >
      <div className="flex items-start gap-2">
        <Link href={subagentHref(sessionId, subagent.id)} className="min-w-0 flex-1 truncate text-[13px] font-medium text-ink hover:text-accent-700" title={subagent.task ?? undefined}>
          {subagentName(subagent)}
        </Link>
        <StatusPill kind={statusKind(subagent.status)} label={statusLabel(subagent.status)} className="shrink-0 text-[12.5px]" />
      </div>
      <div className="flex items-center gap-2 text-xs text-ink-3">
        <span>{kindLabel(subagent.kind)}</span>
        <span className="flex-1" />
        <span className="tabular font-mono">{formatMoney(subagent.cost_usd)}</span>
      </div>
      {working ? (
        <button
          type="button"
          onClick={() => void stop()}
          disabled={stopping}
          className="mt-1 inline-flex h-7 w-fit items-center gap-1 rounded-md border border-line px-2 text-xs font-medium hover:bg-canvas disabled:opacity-40"
        >
          <Stop size={12} weight="fill" /> {stopping ? 'Stopping…' : 'Stop'}
        </button>
      ) : null}
    </li>
  );
}

/** The rail's list of every subagent below the session, each under the one that started it. */
export function SubagentsSection({
  sessionId,
  subagents,
  loading,
}: {
  sessionId: string;
  subagents: Subagent[] | undefined;
  loading: boolean;
}): JSX.Element | null {
  const [open, setOpen] = useState(true);
  if (!loading && !subagents?.length) return null; // most sessions start none: no empty panel
  const ordered = treeOrder(subagents ?? []);
  const working = ordered.filter((s) => s.status === 'running' || s.status === 'starting').length;
  return (
    <Card className="flex flex-col gap-3 px-[18px] py-4" data-testid="rail-subagents">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} className="flex items-baseline justify-between gap-3 text-left">
        <h2 className="inline-flex items-center gap-1.5 text-sm font-semibold">
          {open ? <CaretDown size={12} /> : <CaretRight size={12} />} Subagents
        </h2>
        <span className="text-[12.5px] text-ink-3">{working ? `${working} working · ${ordered.length} in all` : `${ordered.length}`}</span>
      </button>
      {open ? (
        loading && !ordered.length ? (
          <p className="text-[13px] text-ink-2">Loading…</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {ordered.map((s) => (
              <SubagentRow key={s.id} subagent={s} sessionId={sessionId} />
            ))}
          </ul>
        )
      ) : null}
    </Card>
  );
}
