/**
 * One subagent of a session: the task it was given, its own conversation (live while it
 * works), how it ended and what it answered. A person can stop it while it works, and give a
 * continuable one its next task; the agent that started it is told how each turn ends.
 */

'use client';

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { AnimatePresence } from 'framer-motion';
import { CircleNotch, GitBranch, Stop } from '@phosphor-icons/react';
import { toast } from 'sonner';

import { AppShell } from '@/components/layout/AppShell';
import { ApprovalCard, MessageBubble, ToolCard } from '@/components/session/parts';
import { statusKind, SubagentCallCard, SubagentNoticeLine, subagentHref } from '@/components/session/subagents';
import { Card, ErrorNote, Mono, StatusPill } from '@/components/ui';
import { sessionsAPI } from '@/lib/api/endpoints/sessions';
import { useHarness } from '@/lib/api/hooks/useHarnesses';
import { useSession } from '@/lib/api/hooks/useSessions';
import { useSubagents } from '@/lib/api/hooks/useSubagents';
import type { Subagent, ToolCallView } from '@/lib/api/types/session.types';
import { messageTooLong } from '@/lib/session/messageLimit';
import { kindLabel, statusLabel, SUBAGENT_TOOLS, subagentName } from '@/lib/session/subagents';
import { useSessionStream } from '@/lib/session/useSessionStream';
import { formatMoney } from '@/lib/utils';

function StartedBy({ sessionId, subagent, all }: { sessionId: string; subagent: Subagent; all: Subagent[] }): JSX.Element {
  const parent = all.find((s) => s.id === subagent.parent_id);
  return parent ? (
    <span>
      Started by the subagent{' '}
      <Link href={subagentHref(sessionId, parent.id)} className="font-medium text-ink hover:text-accent-700">
        {subagentName(parent)}
      </Link>
    </span>
  ) : (
    <span>
      Started by{' '}
      <Link href={`/sessions/${encodeURIComponent(sessionId)}`} className="font-medium text-ink hover:text-accent-700">
        the agent
      </Link>
    </span>
  );
}

function SubagentPageContent(): JSX.Element {
  const params = useParams();
  const sessionId = typeof params.id === 'string' ? params.id : '';
  const subagentId = typeof params.subagentId === 'string' ? decodeURIComponent(params.subagentId) : '';
  const { data: session } = useSession(sessionId);
  const { data: harness } = useHarness(session?.harness_id ?? '');
  const { state, connection, addLocalMessage } = useSessionStream(sessionId, { subagentId });
  const { data: subagents, isLoading, error, refetch } = useSubagents(sessionId, { sessionRunning: state.running });
  const subagent = subagents?.find((s) => s.id === subagentId);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [stopping, setStopping] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const pendingTools = Array.from(state.pendingTools.values());

  useEffect(() => {
    void refetch(); // its run started or ended: its status and answer changed
  }, [state.running, refetch]);
  useEffect(
    () => endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }),
    [state.messages.length, pendingTools.length],
  );

  if (isLoading) {
    return (
      <div className="flex flex-1 items-center justify-center gap-2 text-sm text-ink-2">
        <CircleNotch size={16} className="animate-spin" /> Loading the subagent…
      </div>
    );
  }
  if (error || !subagent) {
    return (
      <div className="mx-auto mt-16 w-full max-w-md px-4">
        <ErrorNote message="This subagent could not be found. It may belong to another session, or the session was erased." />
        <Link href={`/sessions/${encodeURIComponent(sessionId)}`} className="mt-3 inline-block text-sm font-medium text-accent-700">
          Back to the session
        </Link>
      </div>
    );
  }

  const nameOf = new Map((subagents ?? []).map((s) => [s.id, subagentName(s)]));
  const working = state.running || subagent.status === 'running' || subagent.status === 'starting';
  const takesMessages = subagent.kind === 'continuable' && subagent.open;
  const renderTool = (tool: ToolCallView): JSX.Element =>
    SUBAGENT_TOOLS.has(tool.name) ? (
      <SubagentCallCard key={tool.id} tool={tool} sessionId={sessionId} startedId={state.subagentOf.get(tool.id)} />
    ) : (
      <ToolCard key={tool.id} tool={tool} />
    );

  async function send(e?: React.FormEvent): Promise<void> {
    e?.preventDefault();
    const text = input.trim();
    if (!text || sending || !takesMessages) return;
    const tooLong = messageTooLong(text);
    if (tooLong) {
      toast.error(tooLong);
      return;
    }
    setSending(true);
    addLocalMessage(text);
    setInput('');
    try {
      await sessionsAPI.messageSubagent(sessionId, subagentId, text);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not send it');
    } finally {
      setSending(false);
    }
  }

  async function stop(): Promise<void> {
    setStopping(true);
    try {
      await sessionsAPI.stopSubagent(sessionId, subagentId);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not stop it');
    } finally {
      setStopping(false);
    }
  }

  async function answer(approvalId: string, choice: string): Promise<void> {
    try {
      await sessionsAPI.respondApproval(sessionId, approvalId, choice);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to answer');
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto p-4 sm:p-5 lg:px-6">
      <Card className="flex shrink-0 flex-col gap-3 px-5 py-4" data-testid="subagent-bar">
        <div className="flex flex-wrap items-center gap-2 text-[12.5px] text-ink-3">
          <Link href="/sessions" className="hover:text-ink">
            Sessions
          </Link>
          <span aria-hidden="true">/</span>
          <Link href={`/sessions/${encodeURIComponent(sessionId)}`} className="hover:text-ink">
            {session?.title || (harness ? `${harness.name} session` : 'Session')}
          </Link>
          <span aria-hidden="true">/</span>
          <span>Subagent</span>
          <Mono className="text-[12.5px]">{subagent.id.slice(0, 13)}</Mono>
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <h1 className="inline-flex min-w-0 items-center gap-2 truncate text-[22px] font-semibold tracking-[-0.02em]">
            <GitBranch size={20} className="shrink-0 text-ink-2" aria-hidden="true" />
            {subagentName(subagent)}
          </h1>
          <span className="flex-1" />
          {working ? (
            <button
              type="button"
              onClick={() => void stop()}
              disabled={stopping}
              data-testid="subagent-stop"
              className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-line bg-surface px-3 text-sm font-medium hover:bg-canvas disabled:opacity-40"
            >
              <Stop size={14} weight="fill" /> {stopping ? 'Stopping…' : 'Stop'}
            </button>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-[13px] text-ink-2">
          <StatusPill kind={statusKind(subagent.status)} label={statusLabel(subagent.status)} />
          <span data-testid="subagent-kind">{kindLabel(subagent.kind)}</span>
          {subagent.mode === 'fork' ? <span>Started from the agent&apos;s conversation</span> : null}
          <StartedBy sessionId={sessionId} subagent={subagent} all={subagents ?? []} />
          <span className="tabular font-mono text-[12.5px]" title="What it spent, its own subagents included">
            {formatMoney(subagent.cost_usd)}
          </span>
        </div>
        {subagent.task ? (
          <div className="rounded-[10px] border border-line-soft bg-canvas px-3.5 py-2.5 text-sm">
            <div className="mb-1 text-xs font-medium uppercase tracking-wide text-ink-3">
              {subagent.kind === 'continuable' ? 'Its first task' : 'Its task'}
            </div>
            <p data-testid="subagent-page-task" className="whitespace-pre-wrap break-words">
              {subagent.task}
            </p>
          </div>
        ) : null}
      </Card>

      {subagent.answer && !working ? (
        <Card className="shrink-0 px-5 py-4" data-testid="subagent-answer-card">
          <div className="mb-1 text-xs font-medium uppercase tracking-wide text-ink-3">
            {subagent.outcome === 'completed' ? 'Its answer, as the agent read it' : 'What the agent was told'}
          </div>
          <p className="whitespace-pre-wrap break-words text-sm">{subagent.answer}</p>
          {subagent.answer_truncated ? <p className="mt-1 text-xs text-ink-3">Shortened here; the conversation below has it all.</p> : null}
        </Card>
      ) : null}

      <Card aria-label="Its conversation" className="min-h-[240px] overflow-auto lg:min-h-0 lg:flex-1">
        <div className="flex flex-col gap-5 p-5 sm:p-6">
          {state.messages.length === 0 && pendingTools.length === 0 ? (
            <div className="flex items-center justify-center gap-2 py-16 text-sm text-ink-2">
              {connection === 'connecting' ? (
                <>
                  <CircleNotch size={16} className="animate-spin text-accent-600" /> Loading its conversation…
                </>
              ) : (
                'Nothing yet: it has not started.'
              )}
            </div>
          ) : null}
          <AnimatePresence initial={false}>
            {state.messages.map((m) =>
              m.notice ? (
                <SubagentNoticeLine key={m.id} message={m} sessionId={sessionId} name={nameOf.get(m.notice.subagentId)} />
              ) : (
                <MessageBubble
                  key={m.id}
                  message={m}
                  agentName={subagentName(subagent)}
                  userName="Instructions"
                  userInitials="→"
                  renderTool={renderTool}
                />
              ),
            )}
          </AnimatePresence>
          {pendingTools.length > 0 ? <div className="flex flex-col gap-2 pl-10">{pendingTools.map(renderTool)}</div> : null}
          {state.pendingApprovals.length > 0 ? (
            <div className="flex flex-col gap-3 pl-10">
              {state.pendingApprovals.map((a) => (
                <ApprovalCard
                  key={a.approval_id}
                  prompt={a.prompt}
                  options={a.options}
                  deadline={a.deadline_ms}
                  onAnswer={(choice) => void answer(a.approval_id, choice)}
                />
              ))}
            </div>
          ) : null}
          {state.partial?.text ? (
            <MessageBubble
              key="partial"
              message={{ id: 'partial', role: 'assistant', content: state.partial.text, timestamp: new Date().toISOString() }}
              agentName={subagentName(subagent)}
            />
          ) : null}
          {state.running && !state.partial?.text && pendingTools.every((t) => t.status !== 'running') ? (
            <div className="flex items-center gap-2 pl-10 text-sm text-ink-2" aria-live="polite">
              <CircleNotch size={15} className="animate-spin text-accent-600" /> Working…
            </div>
          ) : null}
          <div ref={endRef} />
        </div>
      </Card>

      {takesMessages ? (
        <form onSubmit={(e) => void send(e)} aria-label="Message the subagent" className="flex shrink-0 flex-col gap-2 rounded-card border border-line bg-surface p-3">
          <label htmlFor={`subagent-composer-${subagentId}`} className="px-1 text-[12.5px] text-ink-2">
            Give it its next task. The agent that started it is told what it answers.
          </label>
          <textarea
            id={`subagent-composer-${subagentId}`}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                void send();
              }
            }}
            rows={2}
            placeholder={working ? 'Send another: it runs after the current task' : 'Send a message…'}
            className="max-h-40 resize-none border-0 bg-transparent p-1 text-base text-ink outline-none placeholder:text-ink-3"
          />
          <div className="flex items-center justify-end">
            <button
              type="submit"
              disabled={!input.trim() || sending}
              aria-label="Send"
              className="h-9 rounded-lg bg-ink px-4 text-sm font-medium text-white hover:bg-ink/90 disabled:cursor-not-allowed disabled:bg-line disabled:text-ink-3"
            >
              Send
            </button>
          </div>
        </form>
      ) : (
        <p className="shrink-0 px-1 text-[13px] text-ink-3" data-testid="subagent-no-messages">
          {subagent.kind === 'continuable'
            ? 'It has ended with its session.'
            : 'It works on the one task it was given and takes no messages. Message the agent instead.'}
        </p>
      )}
    </div>
  );
}

export default function SubagentPage(): JSX.Element {
  return (
    <AppShell fullHeight mainClassName="flex min-h-0 flex-col overflow-hidden">
      <SubagentPageContent />
    </AppShell>
  );
}
