'use client';

/**
 * A live conversation with one session: the journal as it streams (resuming after a
 * dropped connection), tool calls as they run, approvals with their countdown, and the
 * controls a user needs: send (queued behind a running turn), stop, fork, end.
 */

import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { AnimatePresence } from 'framer-motion';
import { CircleNotch, GitBranch, PaperPlaneTilt, Robot, Sparkle, Stop, XCircle } from '@phosphor-icons/react';
import { toast } from 'sonner';

import { sessionsAPI } from '@/lib/api/endpoints/sessions';
import type { Session } from '@/lib/api/types/session.types';
import { useSessionStream, type Connection } from '@/lib/session/useSessionStream';
import { cn } from '@/lib/utils';

import { ApprovalCard, MessageBubble, SubagentChip, ToolCard } from './parts';

const ENDED = new Set(['terminated', 'failed', 'budget_exceeded']);

const CONNECTION_LABEL: Record<Connection, string> = {
  connecting: 'Connecting…',
  live: 'Live',
  reconnecting: 'Reconnecting…',
  closed: 'Closed',
};

export interface SessionChatProps {
  session: Session;
  /** False when the platform or harness cannot run; `blockedReason` says why. */
  canRun?: boolean;
  blockedReason?: string;
  /** The harness version's step limit, to show steps used against it. */
  maxSteps?: number;
  /** What an unanswered approval does (the policy's on_approval_timeout). */
  onApprovalTimeout?: string;
  /** Called when a run ends: its tools may have changed the workspace. */
  onRunEnd?: () => void;
}

export function SessionChat({ session, canRun = true, blockedReason, maxSteps, onApprovalTimeout, onRunEnd }: SessionChatProps): JSX.Element {
  const router = useRouter();
  const [status, setStatus] = useState(session.status);
  const ended = ENDED.has(status);
  const { state, connection, addLocalMessage } = useSessionStream(session.id);
  const [input, setInput] = useState('');
  const [queuedText, setQueuedText] = useState<Set<string>>(new Set());
  const [sending, setSending] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const pendingTools = Array.from(state.pendingTools.values());
  const subagents = Array.from(state.pendingSubagents.values());

  useEffect(() => setStatus(session.status), [session.status]);
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && !state.running) onRunEnd?.();
    wasRunning.current = state.running;
  }, [state.running, onRunEnd]);
  useEffect(() => endRef.current?.scrollIntoView({ behavior: 'smooth' }), [state.messages.length, pendingTools.length]);

  const blocked = ended || !canRun;
  const thinking = state.running && !state.awaitingApproval && pendingTools.every((t) => t.status !== 'running');

  async function send(e?: React.FormEvent): Promise<void> {
    e?.preventDefault();
    const text = input.trim();
    if (!text || blocked || sending) return;
    setSending(true);
    addLocalMessage(text);
    setInput('');
    try {
      const sent = await sessionsAPI.sendMessage(session.id, text);
      if (sent.queued) setQueuedText((q) => new Set(q).add(text));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to send');
    } finally {
      setSending(false);
    }
  }

  async function answer(approvalId: string, choice: string): Promise<void> {
    try {
      await sessionsAPI.respondApproval(session.id, approvalId, choice);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to answer');
    }
  }

  async function stop(): Promise<void> {
    try {
      await sessionsAPI.abort(session.id);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to stop');
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

  // A queued message stops being queued once the journal confirms it (its run started).
  const confirmed = new Set(state.messages.filter((m) => !m.id.startsWith('local-')).map((m) => m.content));
  const isQueued = (content: string, id: string) => id.startsWith('local-') && queuedText.has(content) && !confirmed.has(content);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-slate-200/80 bg-white/80 px-6 py-2 text-xs text-slate-500" data-testid="session-bar">
        <span className="flex items-center gap-1.5">
          <span className={cn('h-2 w-2 rounded-full', connection === 'live' ? 'bg-secondary-500' : connection === 'closed' ? 'bg-slate-300' : 'animate-pulse bg-amber-500')} />
          <span data-testid="connection">{ended ? 'Ended' : CONNECTION_LABEL[connection]}</span>
        </span>
        <span data-testid="steps">
          Steps {state.runSteps}
          {maxSteps ? ` / ${maxSteps}` : ''}
        </span>
        <span>{(state.usage.input_tokens + state.usage.output_tokens).toLocaleString()} tokens</span>
        <span data-testid="cost">${Math.max(state.usage.cost, session.total_cost).toFixed(4)}</span>
        <span className="ml-auto flex items-center gap-1">
          {state.running && !ended ? (
            <button type="button" onClick={() => void stop()} className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-red-600 hover:bg-red-50">
              <Stop size={14} weight="fill" /> Stop
            </button>
          ) : null}
          <button type="button" onClick={() => void fork()} disabled={state.lastSeq < 0} className="inline-flex items-center gap-1 rounded-lg px-2 py-1 hover:bg-slate-100 disabled:opacity-40">
            <GitBranch size={14} /> Fork
          </button>
          {!ended ? (
            <button type="button" onClick={() => void end()} className="inline-flex items-center gap-1 rounded-lg px-2 py-1 hover:bg-slate-100">
              <XCircle size={14} /> End
            </button>
          ) : null}
        </span>
      </div>

      {blocked && !ended && blockedReason ? (
        <div className="border-b border-amber-200 bg-amber-50 px-6 py-2 text-center text-sm text-amber-800">{blockedReason}</div>
      ) : null}
      {state.error ? (
        <div className="border-b border-red-200 bg-red-50 px-6 py-2 text-center text-sm text-red-700" data-testid="session-error">{state.error}</div>
      ) : null}
      {state.pendingApprovals.length > 0 ? (
        <div className="border-b border-amber-300 bg-amber-50 px-6 py-4">
          <div className="mx-auto flex max-w-3xl flex-col gap-3">
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
          </div>
        </div>
      ) : null}

      <div className="flex-1 overflow-auto">
        <div className="mx-auto max-w-3xl px-6 py-8">
          {state.messages.length === 0 && pendingTools.length === 0 ? (
            connection === 'connecting' ? (
              <div className="flex items-center justify-center gap-2 py-20 text-sm text-slate-500">
                <CircleNotch size={16} className="animate-spin text-primary-500" /> Loading the conversation…
              </div>
            ) : (
              <div className="flex flex-col items-center py-20 text-center">
                <div className="flex h-16 w-16 items-center justify-center rounded-3xl bg-gradient-to-br from-primary-500 to-primary-600 shadow-xl shadow-primary-500/30">
                  <Sparkle size={30} weight="fill" className="text-white" />
                </div>
                <h2 className="mt-5 text-lg font-semibold text-slate-900">Start a conversation</h2>
                <p className="mt-1 max-w-sm text-sm text-slate-500">The agent answers with the prompt, tools and policy of the version this session runs.</p>
              </div>
            )
          ) : null}

          <div className="space-y-6">
            <AnimatePresence initial={false}>
              {state.messages.map((m) => (
                <MessageBubble
                  key={m.id}
                  message={m}
                  queued={isQueued(m.content, m.id)}
                  onFork={m.role === 'assistant' && m.seq != null ? () => void fork(m.seq) : undefined}
                />
              ))}
            </AnimatePresence>
            {pendingTools.length > 0 ? (
              <div className="space-y-2 pl-14">
                {pendingTools.map((tool) => (
                  <ToolCard key={tool.id} tool={tool} />
                ))}
              </div>
            ) : null}
            {subagents.length > 0 ? (
              <div className="space-y-2 pl-14">
                {subagents.map((chip) => (
                  <SubagentChip key={chip.run_id} chip={chip} sessionId={session.id} />
                ))}
              </div>
            ) : null}
            {thinking ? (
              <div className="flex items-start gap-4">
                <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-primary-500 to-primary-600 shadow-md">
                  <Robot size={18} weight="fill" className="text-white" />
                </div>
                <div className="flex items-center gap-3 rounded-2xl border border-slate-200 bg-white px-5 py-3 shadow-sm">
                  <CircleNotch size={16} className="animate-spin text-primary-500" />
                  <span className="text-sm text-slate-500">Thinking…</span>
                </div>
              </div>
            ) : null}
            <div ref={endRef} />
          </div>
        </div>
      </div>

      <div className="border-t border-slate-200 bg-white/90 backdrop-blur-md">
        <form onSubmit={(e) => void send(e)} className="mx-auto max-w-3xl px-6 py-5">
          <div className="flex items-end gap-3 rounded-2xl border-2 border-slate-200 bg-white p-3 shadow-lg shadow-slate-900/5 focus-within:border-primary-400">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  void send();
                }
              }}
              rows={1}
              disabled={blocked}
              placeholder={ended ? 'This session has ended. Fork it to continue.' : blocked ? blockedReason ?? 'Cannot run right now' : state.running ? 'Send another: it runs after the current turn' : 'Send a message…'}
              className="max-h-36 flex-1 resize-none bg-transparent px-2 py-2 text-[15px] text-slate-900 outline-none placeholder:text-slate-400 disabled:cursor-not-allowed"
            />
            <button
              type="submit"
              disabled={!input.trim() || blocked || sending}
              aria-label="Send"
              className={cn('flex h-11 w-11 items-center justify-center rounded-xl transition-all', input.trim() && !blocked ? 'bg-gradient-to-br from-primary-500 to-primary-600 text-white shadow-lg shadow-primary-500/25' : 'cursor-not-allowed bg-slate-100 text-slate-400')}
            >
              <PaperPlaneTilt size={18} weight="fill" />
            </button>
          </div>
          <p className="mt-2 text-center text-xs text-slate-400">Enter to send · Shift+Enter for a new line</p>
        </form>
      </div>
    </div>
  );
}
