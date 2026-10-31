'use client';

/**
 * A live conversation with one session: the journal as it streams (resuming after a
 * dropped connection), tool calls as they run, approvals with their countdown, and the
 * controls a user needs: send (queued behind a running turn), stop, fork, end. The right
 * rail shows its sandboxes and what it has used.
 */

import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { AnimatePresence } from 'framer-motion';
import { CircleNotch, ClockCounterClockwise, GitBranch, Stop, TerminalWindow, Trash, XCircle } from '@phosphor-icons/react';
import { toast } from 'sonner';

import { Card, StatusPill, type StatusKind } from '@/components/ui';
import { sessionsAPI } from '@/lib/api/endpoints/sessions';
import { useSubagentApprovals } from '@/lib/api/hooks/useSubagentApprovals';
import { useSubagents } from '@/lib/api/hooks/useSubagents';
import type { Session, ToolCallView } from '@/lib/api/types/session.types';
import { messageTooLong } from '@/lib/session/messageLimit';
import { runToStop, sandboxLabel } from '@/lib/session/runs';
import { SUBAGENT_TOOLS, subagentName } from '@/lib/session/subagents';
import { useSessionStream, type Connection } from '@/lib/session/useSessionStream';
import { useAuthStore } from '@/lib/store/authStore';
import { cn, formatMoney } from '@/lib/utils';

import { ApprovalCard, MessageBubble, ToolCard } from './parts';
import { RunHistory } from './RunHistory';
import { SessionRail } from './SessionRail';
import { SubagentCallCard, SubagentNoticeLine, SubagentsSection } from './subagents';

const ENDED = new Set(['terminated', 'failed', 'budget_exceeded']);

const CONNECTION_LABEL: Record<Connection, string> = {
  connecting: 'Connecting…',
  live: 'Live',
  reconnecting: 'Reconnecting…',
  closed: 'Closed',
  ended: 'Ended',
};

export interface SessionHeading {
  /** Above the title: a breadcrumb back. */
  crumb?: React.ReactNode;
  title: React.ReactNode;
  /** Beside the status: agent@version, hash, when it started. */
  meta?: React.ReactNode;
}

export interface SessionChatProps {
  session: Session;
  /** False when the platform or harness cannot run; `blockedReason` says why. */
  canRun?: boolean;
  blockedReason?: string;
  /** The harness version's step limit, to show steps used against it. */
  maxSteps?: number;
  /** The version's spend cap per session (dollars), for the budget meter. */
  maxCost?: number;
  /** What an unanswered approval does (the policy's on_approval_timeout). */
  onApprovalTimeout?: string;
  /** Called when a run ends: its tools may have changed the workspace. */
  onRunEnd?: () => void;
  heading?: SessionHeading;
  /** The agent's name, on its messages and the composer. */
  agentName?: string;
  /** More panels for the rail (files, the workspace). */
  railExtra?: React.ReactNode;
}

function initials(name: string | undefined | null): string {
  const parts = (name ?? '').trim().split(/\s+/).filter(Boolean);
  return (parts.length > 1 ? parts[0][0] + parts[1][0] : (parts[0] ?? 'Y').slice(0, 1)).toUpperCase();
}

export function SessionChat({
  session,
  canRun = true,
  blockedReason,
  maxSteps,
  maxCost,
  onApprovalTimeout,
  onRunEnd,
  heading,
  agentName,
  railExtra,
}: SessionChatProps): JSX.Element {
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const [status, setStatus] = useState(session.status);
  const ended = ENDED.has(status);
  const { state, connection, addLocalMessage } = useSessionStream(session.id);

  // The stream says when the session ended elsewhere (another tab, a failure): show how.
  useEffect(() => {
    if (connection !== 'ended' || ended) return;
    sessionsAPI
      .getById(session.id)
      .then((latest) => setStatus(latest.status))
      .catch(() => setStatus('terminated'));
  }, [connection, ended, session.id]);
  const [input, setInput] = useState('');
  /** Queued messages by text: the run id each got, and whether that run was cancelled. */
  const [queuedRuns, setQueuedRuns] = useState<Map<string, { runId: string; cancelled: boolean }>>(new Map());
  const [sending, setSending] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [showRuns, setShowRuns] = useState(false);
  const [runsKey, setRunsKey] = useState(0);
  const endRef = useRef<HTMLDivElement>(null);
  const pendingTools = Array.from(state.pendingTools.values());
  const { data: subagents, isLoading: subagentsLoading, refetch: refetchSubagents } = useSubagents(session.id, {
    sessionRunning: state.running,
  });
  const nameOf = new Map((subagents ?? []).map((s) => [s.id, subagentName(s)]));
  // A subagent's call that waits on a person shows here too: it is answered from this session.
  const { approvals: subagentApprovals, answered: subagentAnswered } = useSubagentApprovals(session.id, {
    active: state.running || (subagents?.length ?? 0) > 0,
  });
  const renderTool = (tool: ToolCallView): JSX.Element =>
    SUBAGENT_TOOLS.has(tool.name) ? (
      <SubagentCallCard key={tool.id} tool={tool} sessionId={session.id} startedId={state.subagentOf.get(tool.id)} />
    ) : (
      <ToolCard key={tool.id} tool={tool} />
    );

  useEffect(() => setStatus(session.status), [session.status]);
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && !state.running) onRunEnd?.();
    if (wasRunning.current !== state.running) {
      setRunsKey((k) => k + 1);
      void refetchSubagents(); // a run starts or ends subagents
    }
    wasRunning.current = state.running;
  }, [state.running, onRunEnd, refetchSubagents]);
  useEffect(
    () => endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }),
    [state.messages.length, pendingTools.length, state.pendingApprovals.length],
  );

  const blocked = ended || !canRun;
  const thinking = state.running && !state.awaitingApproval && pendingTools.every((t) => t.status !== 'running');

  const kind: StatusKind = ended
    ? status === 'terminated'
      ? 'done'
      : 'failed'
    : state.pendingApprovals.length > 0 || subagentApprovals.length > 0
      ? 'awaiting'
      : state.running
        ? 'running'
        : 'idle';
  const kindLabel = ended ? (status === 'terminated' ? 'Ended' : status === 'budget_exceeded' ? 'Over budget' : 'Failed') : kind === 'idle' ? 'Waiting for you' : undefined;

  async function send(e?: React.FormEvent): Promise<void> {
    e?.preventDefault();
    const text = input.trim();
    if (!text || blocked || sending) return;
    const tooLong = messageTooLong(text);
    if (tooLong) {
      toast.error(tooLong); // kept in the box, so nothing is lost
      return;
    }
    setSending(true);
    addLocalMessage(text);
    setInput('');
    try {
      const sent = await sessionsAPI.sendMessage(session.id, text);
      if (sent.queued) setQueuedRuns((q) => new Map(q).set(text, { runId: sent.turn_id, cancelled: false }));
      if (sent.held) toast.info('No worker has taken your message yet. It is kept and runs as soon as one does.');
      setRunsKey((k) => k + 1);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to send');
    } finally {
      setSending(false);
    }
  }

  /** Whether the answer was taken. */
  async function answer(approvalId: string, choice: string): Promise<boolean> {
    try {
      await sessionsAPI.respondApproval(session.id, approvalId, choice);
      return true;
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to answer');
      return false;
    }
  }


  // Stop cancels the run going now, by its id: safe to press twice, and it never stops a
  // later run by mistake. Without a run to name (none recorded yet), it aborts the session's turn.
  async function stop(): Promise<void> {
    if (stopping) return;
    setStopping(true);
    try {
      const run = runToStop(await sessionsAPI.runs(session.id, 5));
      if (run) await sessionsAPI.cancelRun(session.id, run.run_id);
      else await sessionsAPI.abort(session.id);
      setRunsKey((k) => k + 1);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to stop');
    } finally {
      setStopping(false);
    }
  }

  async function fork(atSeq?: number): Promise<void> {
    try {
      const child = await sessionsAPI.fork(session.id, atSeq);
      toast.success('Forked: a new session with the conversation so far');
      router.push(`/sessions/${child.id}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to fork');
    }
  }

  async function end(): Promise<void> {
    if (!window.confirm('End this session? It cannot be continued afterwards.')) return;
    try {
      await sessionsAPI.delete(session.id);
      setStatus('terminated');
      toast.success('Session ended');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to end the session');
    }
  }

  async function erase(): Promise<void> {
    const sure = window.confirm(
      'Erase this session? Its messages, files and history are deleted for good, and it cannot be recovered.',
    );
    if (!sure) return;
    try {
      await sessionsAPI.erase(session.id);
      toast.success('Session erased');
      router.push('/sessions');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to erase the session');
    }
  }

  // A queued message stops being queued once the journal confirms it (its run started), or
  // once its run is cancelled (it then never runs).
  const confirmed = new Set(state.messages.filter((m) => !m.id.startsWith('local-')).map((m) => m.content));
  const queuedRun = (content: string, id: string) => (id.startsWith('local-') && !confirmed.has(content) ? queuedRuns.get(content) : undefined);

  function markCancelled(runId: string): void {
    setQueuedRuns((q) => {
      const next = new Map(q);
      for (const [text, v] of q) if (v.runId === runId) next.set(text, { runId, cancelled: true });
      return next;
    });
  }

  async function cancelQueued(content: string, runId: string): Promise<void> {
    try {
      const run = await sessionsAPI.cancelRun(session.id, runId);
      if (run.status === 'cancelled') markCancelled(runId);
      setRunsKey((k) => k + 1);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not cancel the message');
    }
  }

  const sandboxNote = sandboxLabel(state.sandbox);
  const ctl = 'inline-flex h-9 items-center gap-1.5 rounded-lg border border-line bg-surface px-3 text-sm font-medium text-ink hover:bg-canvas disabled:opacity-40';

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-5 overflow-auto p-4 sm:p-5 lg:flex-row lg:overflow-hidden lg:px-6">
      <div className="flex min-w-0 flex-col gap-4 lg:min-h-0 lg:flex-[999_1_560px]">
        <Card className="flex shrink-0 flex-wrap items-center gap-x-6 gap-y-3.5 px-5 py-4" data-testid="session-bar">
          <div className="min-w-0 flex-[1_1_360px]">
            {heading?.crumb ? <div className="flex items-center gap-2 text-[12.5px] text-ink-3">{heading.crumb}</div> : null}
            {heading ? <h1 className="mb-1.5 mt-1 truncate text-[22px] font-semibold tracking-[-0.02em]">{heading.title}</h1> : null}
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-[13px] text-ink-2">
              <StatusPill kind={kind} label={kindLabel} className={kind === 'awaiting' ? 'py-[3px]' : undefined} />
              <span className="inline-flex items-center gap-1.5" title="Event stream">
                <span
                  aria-hidden="true"
                  className={cn(
                    'h-1.5 w-1.5 rounded-full',
                    ended || connection === 'closed' || connection === 'ended' ? 'bg-ink-3' : connection === 'live' ? 'bg-good' : 'animate-pulse bg-warn',
                  )}
                />
                <span data-testid="connection">{ended ? 'Ended' : CONNECTION_LABEL[connection]}</span>
              </span>
              {heading?.meta}
              <span data-testid="steps" className="tabular">
                Steps {state.runSteps}
                {maxSteps ? ` / ${maxSteps}` : ''}
              </span>
              <span data-testid="cost" className="tabular font-mono text-[12.5px]">
                {formatMoney(Math.max(state.usage.cost, session.total_cost))}
              </span>
              {sandboxNote ? (
                <span data-testid="sandbox-status" data-status={state.sandbox?.status} className="text-ink-3">
                  {sandboxNote}
                </span>
              ) : null}
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {state.running && !ended ? (
              <button type="button" onClick={() => void stop()} disabled={stopping} className={ctl} data-testid="stop">
                <Stop size={14} weight="fill" /> {stopping ? 'Stopping…' : 'Stop'}
              </button>
            ) : null}
            <button
              type="button"
              onClick={() => setShowRuns((v) => !v)}
              aria-expanded={showRuns}
              className={cn(ctl, showRuns && 'border-accent-200 bg-accent-50 text-accent-700')}
              data-testid="runs-toggle"
            >
              <ClockCounterClockwise size={14} /> Runs
            </button>
            <button type="button" onClick={() => void fork()} disabled={state.lastSeq < 0} className={ctl}>
              <GitBranch size={14} /> Fork
            </button>
            {!ended ? (
              <button type="button" onClick={() => void end()} className={ctl}>
                <XCircle size={14} /> End
              </button>
            ) : null}
            <button type="button" onClick={() => void erase()} className={cn(ctl, 'text-bad')} data-testid="erase">
              <Trash size={14} /> Erase
            </button>
          </div>
        </Card>

        {showRuns ? (
          <Card className="shrink-0">
            <RunHistory sessionId={session.id} refreshKey={runsKey} onCancelled={markCancelled} />
          </Card>
        ) : null}
        {blocked && !ended && blockedReason ? (
          <div role="status" className="shrink-0 rounded-card border border-line bg-surface px-4 py-2.5 text-sm text-ink-2">
            {blockedReason}
          </div>
        ) : null}
        {state.error ? (
          <div role="alert" className="flex shrink-0 items-center gap-3 rounded-card border border-line bg-surface px-4 py-2.5 text-sm" data-testid="session-error">
            <span className="h-2 w-2 shrink-0 rotate-45 bg-bad" aria-hidden="true" />
            {state.error}
          </div>
        ) : null}

        <Card aria-label="Conversation" className="max-h-[70vh] min-h-[240px] overflow-auto lg:max-h-none lg:min-h-0 lg:flex-1">
          <div className="flex flex-col gap-5 p-5 sm:p-6">
            {state.messages.length === 0 && pendingTools.length === 0 ? (
              connection === 'connecting' ? (
                <div className="flex items-center justify-center gap-2 py-16 text-sm text-ink-2">
                  <CircleNotch size={16} className="animate-spin text-accent-600" /> Loading the conversation…
                </div>
              ) : (
                <div className="flex flex-col items-center py-16 text-center">
                  <div className="flex h-10 w-10 items-center justify-center rounded-[10px] bg-ink text-white">
                    <TerminalWindow size={20} />
                  </div>
                  <h2 className="mt-4 font-semibold">Start a conversation</h2>
                  <p className="mt-1 max-w-sm text-sm text-ink-2">
                    The agent answers with the prompt, tools and policy of the version this session runs.
                  </p>
                </div>
              )
            ) : null}

            <AnimatePresence initial={false}>
              {state.messages.map((m) =>
                m.notice ? (
                  <SubagentNoticeLine key={m.id} message={m} sessionId={session.id} name={nameOf.get(m.notice.subagentId)} />
                ) : (
                <MessageBubble
                  key={m.id}
                  message={m}
                  renderTool={renderTool}
                  agentName={agentName}
                  userInitials={initials(user?.name)}
                  queued={queuedRun(m.content, m.id)?.cancelled === false}
                  cancelled={queuedRun(m.content, m.id)?.cancelled === true}
                  onCancel={queuedRun(m.content, m.id) ? () => void cancelQueued(m.content, queuedRun(m.content, m.id)!.runId) : undefined}
                  onFork={m.role === 'assistant' && m.seq != null ? () => void fork(m.seq) : undefined}
                />
                ),
              )}
            </AnimatePresence>
            {pendingTools.length > 0 ? (
              <div className="flex flex-col gap-2 pl-10">
                {pendingTools.map(renderTool)}
              </div>
            ) : null}
            {state.pendingApprovals.length > 0 || subagentApprovals.length > 0 ? (
              <div className="flex flex-col gap-3 pl-10">
                {state.pendingApprovals.map((a) => (
                  <ApprovalCard
                    key={a.approval_id}
                    prompt={a.prompt}
                    options={a.options}
                    deadline={a.deadline_ms}
                    onTimeout={onApprovalTimeout}
                    onAnswer={(choice) => void answer(a.approval_id, choice)}
                  />
                ))}
                {subagentApprovals.map((a) => (
                  <ApprovalCard
                    key={a.approval_id}
                    prompt={a.prompt}
                    options={a.options}
                    onTimeout={onApprovalTimeout}
                    from={a.subagent_label ? `Asked by its subagent · ${a.subagent_label}` : 'Asked by its subagent'}
                    onAnswer={(choice) => void answer(a.approval_id, choice).then((ok) => ok && subagentAnswered(a.approval_id))}
                  />
                ))}
              </div>
            ) : null}
            {state.partial?.text ? (
              <MessageBubble
                key="partial"
                message={{ id: 'partial', role: 'assistant', content: state.partial.text, timestamp: new Date().toISOString() }}
                agentName={agentName}
                userInitials={initials(user?.name)}
              />
            ) : null}
            {thinking && !state.partial?.text ? (
              <div className="flex items-start gap-2 pl-10 text-sm text-ink-2" aria-live="polite">
                <CircleNotch size={15} className="mt-0.5 shrink-0 animate-spin text-accent-600" />
                {state.partial?.thinking ? (
                  <span className="line-clamp-3 whitespace-pre-wrap italic">{state.partial.thinking.slice(-400)}</span>
                ) : (
                  'Thinking…'
                )}
              </div>
            ) : null}
            <div ref={endRef} />
          </div>
        </Card>

        <form onSubmit={(e) => void send(e)} aria-label="Message the agent" className="flex shrink-0 flex-col gap-2 rounded-card border border-line bg-surface p-3 focus-within:border-line-strong">
          <label htmlFor={`composer-${session.id}`} className="px-1 text-[12.5px] text-ink-2">
            Message {agentName || 'the agent'}
          </label>
          <textarea
            id={`composer-${session.id}`}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                void send();
              }
            }}
            rows={2}
            disabled={blocked}
            placeholder={
              ended
                ? 'This session has ended. Fork it to continue.'
                : blocked
                  ? blockedReason ?? 'Cannot run right now'
                  : state.running
                    ? 'Send another: it runs after the current turn'
                    : 'Send a message…'
            }
            className="max-h-40 resize-none border-0 bg-transparent p-1 text-base text-ink outline-none placeholder:text-ink-3 disabled:cursor-not-allowed"
          />
          <div className="flex items-center gap-2">
            <span className="text-xs text-ink-3">
              {state.pendingApprovals.length
                ? 'Paused on an approval · messages queue'
                : state.running
                  ? 'Running · messages queue behind this turn'
                  : 'Enter to send · Shift+Enter for a new line'}
            </span>
            <span className="flex-1" />
            <button
              type="submit"
              disabled={!input.trim() || blocked || sending}
              aria-label="Send"
              className="h-9 rounded-lg bg-ink px-4 text-sm font-medium text-white hover:bg-ink/90 disabled:cursor-not-allowed disabled:bg-line disabled:text-ink-3"
            >
              Send
            </button>
          </div>
        </form>
      </div>

      <div className="flex min-w-0 flex-col lg:min-h-0 lg:w-[340px] lg:shrink-0 lg:overflow-auto xl:w-[360px]">
        <SessionRail
          sessionId={session.id}
          live={state.sandbox}
          usage={state.usage}
          steps={state.runSteps}
          maxSteps={maxSteps}
          maxCost={maxCost}
          sessionCost={session.total_cost}
          lead={<SubagentsSection sessionId={session.id} subagents={subagents} loading={subagentsLoading} />}
        >
          {railExtra}
        </SessionRail>
      </div>
    </div>
  );
}
