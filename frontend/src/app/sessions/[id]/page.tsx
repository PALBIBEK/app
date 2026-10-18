/**
 * Session Trace Viewer — getById + SSE event timeline (D4 Traces MVP).
 */

'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { motion } from 'framer-motion';
import {
  ArrowLeft,
  Timer,
  Coins,
  ChatCircle,
  User,
  Robot,
  Wrench,
  CheckCircle,
  XCircle,
  Warning,
  CircleNotch,
  CaretDown,
  CaretUp,
  GitBranch,
} from '@phosphor-icons/react';
import { AuthGuard } from '@/components/auth/AuthGuard';
import { Header } from '@/components/layout/Header';
import { Sidebar } from '@/components/layout/Sidebar';
import { useSession } from '@/lib/api/hooks/useSessions';
import { sessionsAPI } from '@/lib/api/endpoints/sessions';
import type { WireEvent } from '@/lib/api/types/session.types';
import {
  applyWireEvent,
  createSessionViewState,
  displaySubagentProvider,
  type SessionViewState,
} from '@/lib/session/wireEventReducer';
import { cn } from '@/lib/utils';

const pageVariants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { staggerChildren: 0.08 } },
};

const itemVariants = {
  hidden: { opacity: 0, y: 16 },
  visible: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.4, ease: [0.25, 0.46, 0.45, 0.94] },
  },
};

const messageVariants = {
  hidden: { opacity: 0, x: -20 },
  visible: { opacity: 1, x: 0, transition: { duration: 0.3, ease: 'easeOut' } },
};

export default function SessionTracePage(): JSX.Element {
  const params = useParams();
  const sessionId = typeof params.id === 'string' ? params.id : '';
  const { data: session, isLoading, error } = useSession(sessionId);
  const [viewState, setViewState] = useState<SessionViewState>(() => createSessionViewState());
  const [events, setEvents] = useState<WireEvent[]>([]);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [expandedTools, setExpandedTools] = useState<Set<string>>(new Set());
  const [showRaw, setShowRaw] = useState(false);

  useEffect(() => {
    if (!sessionId) return;

    const ac = new AbortController();
    let cancelled = false;
    setViewState(createSessionViewState());
    setEvents([]);
    setStreamError(null);

    (async () => {
      try {
        for await (const event of sessionsAPI.openEventStream(sessionId, {
          signal: ac.signal,
        })) {
          if (cancelled) break;
          setEvents((prev) => [...prev, event]);
          setViewState((prev) => applyWireEvent(prev, event));
        }
      } catch (err) {
        if (cancelled || (err instanceof DOMException && err.name === 'AbortError')) return;
        setStreamError(err instanceof Error ? err.message : 'Event stream failed');
      }
    })();

    return () => {
      cancelled = true;
      ac.abort();
    };
  }, [sessionId]);

  const toggleTool = (toolId: string) => {
    setExpandedTools((prev) => {
      const next = new Set(prev);
      if (next.has(toolId)) next.delete(toolId);
      else next.add(toolId);
      return next;
    });
  };

  const formatTime = (iso: string): string => {
    return new Date(iso).toLocaleTimeString('en-US', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  };

  const status = session?.status ?? '—';
  const isOk = status === 'terminated' || status === 'active';
  const isFail = status === 'failed' || status === 'budget_exceeded';

  const stats = [
    {
      label: 'Messages',
      value: String(session?.message_count ?? viewState.messages.length),
      icon: ChatCircle,
    },
    { label: 'Last seq', value: String(session?.last_seq ?? viewState.lastSeq), icon: Timer },
    {
      label: 'Events',
      value: String(events.length),
      icon: Wrench,
    },
    {
      label: 'Cost',
      value: `$${(session?.total_cost ?? 0).toFixed(2)}`,
      icon: Coins,
      accent: 'green' as const,
    },
  ];

  return (
    <AuthGuard>
      <div className="grid min-h-[100dvh] grid-cols-[auto_1fr] grid-rows-[auto_1fr] bg-gradient-subtle">
        <div className="col-span-2">
          <Header />
        </div>
        <Sidebar />

        <main className="overflow-auto">
          <motion.div
            className="mx-auto max-w-5xl px-8 py-8"
            initial="hidden"
            animate="visible"
            variants={pageVariants}
          >
            <motion.div variants={itemVariants}>
              <Link
                href={session ? `/harness/${session.harness_id}/sessions` : '/sessions'}
                className="group inline-flex items-center gap-2 text-sm font-medium text-slate-500 transition-colors hover:text-slate-700"
              >
                <ArrowLeft size={16} className="transition-transform group-hover:-translate-x-1" />
                Back to Sessions
              </Link>
            </motion.div>

            {isLoading && (
              <p className="mt-8 flex items-center gap-2 text-sm text-slate-500">
                <CircleNotch size={16} className="animate-spin" />
                Loading session…
              </p>
            )}
            {error && (
              <p className="mt-8 text-sm text-red-600">
                {error instanceof Error ? error.message : 'Failed to load session'}
              </p>
            )}
            {streamError && <p className="mt-4 text-sm text-red-600">{streamError}</p>}
            {viewState.error && <p className="mt-4 text-sm text-red-600">{viewState.error}</p>}

            {session && (
              <>
                <motion.div variants={itemVariants} className="mt-6">
                  <div className="flex items-start justify-between">
                    <div className="flex items-start gap-4">
                      <motion.div
                        whileHover={{ scale: 1.05, rotate: -5 }}
                        className={cn(
                          'flex h-14 w-14 items-center justify-center rounded-2xl shadow-lg',
                          isOk &&
                            'bg-gradient-to-br from-secondary-400 to-secondary-600 shadow-secondary-500/25',
                          isFail &&
                            'bg-gradient-to-br from-red-400 to-red-600 shadow-red-500/25',
                          !isOk &&
                            !isFail &&
                            'bg-gradient-to-br from-primary-400 to-primary-600 shadow-primary-500/25'
                        )}
                      >
                        {isFail ? (
                          <XCircle size={26} weight="fill" className="text-white" />
                        ) : (
                          <CheckCircle size={26} weight="fill" className="text-white" />
                        )}
                      </motion.div>
                      <div>
                        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
                          Session Trace
                        </h1>
                        <p className="mt-1 text-sm text-slate-500">
                          {session.harness_id} • {session.id}
                        </p>
                        <p className="mt-1 text-xs font-medium capitalize text-slate-600">
                          {status.replace('_', ' ')}
                          {session.is_busy ? ' · busy' : ''}
                        </p>
                      </div>
                    </div>

                    <motion.button
                      whileHover={{ scale: 1.02 }}
                      whileTap={{ scale: 0.98 }}
                      type="button"
                      onClick={() => setShowRaw((v) => !v)}
                      className={cn(
                        'flex items-center gap-2 rounded-xl px-4 py-2.5',
                        'text-sm font-medium text-slate-600',
                        'bg-white border-2 border-slate-200',
                        'shadow-sm transition-all hover:border-slate-300 hover:shadow'
                      )}
                    >
                      {showRaw ? 'Hide raw events' : 'Show raw events'}
                    </motion.button>
                  </div>
                </motion.div>

                <motion.div variants={itemVariants} className="mt-8 grid grid-cols-4 gap-4">
                  {stats.map((stat) => {
                    const Icon = stat.icon;
                    return (
                      <motion.div
                        key={stat.label}
                        whileHover={{ y: -4, scale: 1.02 }}
                        className={cn(
                          'rounded-2xl bg-white p-5',
                          'border-2 border-slate-200/80',
                          'shadow-sm transition-all hover:border-slate-300 hover:shadow-md'
                        )}
                      >
                        <div className="flex items-center gap-3">
                          <div
                            className={cn(
                              'flex h-10 w-10 items-center justify-center rounded-xl',
                              stat.accent === 'green' ? 'bg-secondary-50' : 'bg-slate-100'
                            )}
                          >
                            <Icon
                              size={18}
                              weight="fill"
                              className={
                                stat.accent === 'green' ? 'text-secondary-600' : 'text-slate-500'
                              }
                            />
                          </div>
                          <div>
                            <p
                              className={cn(
                                'text-xl font-semibold',
                                stat.accent === 'green'
                                  ? 'text-secondary-600'
                                  : 'text-slate-900'
                              )}
                            >
                              {stat.value}
                            </p>
                            <p className="text-xs text-slate-500">{stat.label}</p>
                          </div>
                        </div>
                      </motion.div>
                    );
                  })}
                </motion.div>

                <motion.div variants={itemVariants} className="mt-8">
                  <h2 className="text-lg font-semibold tracking-tight text-slate-900">
                    Conversation Timeline
                  </h2>
                </motion.div>

                {Array.from(viewState.pendingSubagents.values()).length > 0 && (
                  <div className="mt-4 space-y-2">
                    {Array.from(viewState.pendingSubagents.values()).map((chip) => {
                      const provider = displaySubagentProvider(chip.provider);
                      return (
                        <div
                          key={chip.run_id}
                          className="flex items-center gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm"
                        >
                          <GitBranch size={16} className="text-amber-700" />
                          <div className="min-w-0 flex-1">
                            <p className="font-medium text-slate-800">
                              subagent_{chip.status === 'running' ? 'start' : 'end'}
                              {chip.label ? ` · ${chip.label}` : ''}
                            </p>
                            <p className="text-xs text-slate-500">
                              {chip.mode} · {provider.label}
                              {provider.isLab ? ' · Lab' : ''}
                              {chip.stop_reason ? ` · ${chip.stop_reason}` : ''}
                            </p>
                          </div>
                          {chip.child_session_id && (
                            <Link
                              href={`/subagents/${encodeURIComponent(chip.child_session_id)}?session=${encodeURIComponent(sessionId)}`}
                              className="text-xs font-medium text-primary-600"
                            >
                              Open
                            </Link>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}

                <div className="mt-6 space-y-4">
                  {viewState.messages.length === 0 && events.length === 0 && (
                    <p className="text-sm text-slate-500">Waiting for events…</p>
                  )}

                  {viewState.messages.map((message, index) => (
                    <motion.div
                      key={message.id}
                      variants={messageVariants}
                      initial="hidden"
                      animate="visible"
                      transition={{ delay: index * 0.05 }}
                    >
                      <div className="flex items-start gap-4">
                        <motion.div
                          whileHover={{ scale: 1.1, rotate: 5 }}
                          className={cn(
                            'flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl shadow-md',
                            message.role === 'user'
                              ? 'bg-gradient-to-br from-primary-400 to-primary-600 shadow-primary-500/25'
                              : 'bg-gradient-to-br from-secondary-400 to-secondary-600 shadow-secondary-500/25'
                          )}
                        >
                          {message.role === 'user' ? (
                            <User size={18} weight="fill" className="text-white" />
                          ) : (
                            <Robot size={18} weight="fill" className="text-white" />
                          )}
                        </motion.div>

                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="text-sm font-semibold text-slate-900 capitalize">
                              {message.role}
                            </span>
                            <span className="text-xs text-slate-400">
                              {formatTime(message.timestamp)}
                            </span>
                          </div>

                          <motion.div
                            whileHover={{ scale: 1.002 }}
                            className={cn(
                              'mt-2 rounded-2xl p-4',
                              'border-2 border-slate-200/80 bg-white',
                              'shadow-sm transition-all hover:border-slate-300 hover:shadow'
                            )}
                          >
                            <p className="text-sm text-slate-700 leading-relaxed whitespace-pre-wrap">
                              {message.content}
                            </p>
                          </motion.div>

                          {message.toolCalls && message.toolCalls.length > 0 && (
                            <div className="mt-3 space-y-2">
                              {message.toolCalls.map((tool) => {
                                const isExpanded = expandedTools.has(tool.id);
                                return (
                                  <motion.div
                                    key={tool.id}
                                    whileHover={{ scale: 1.002 }}
                                    className={cn(
                                      'rounded-xl border-2 bg-white overflow-hidden',
                                      tool.status === 'error'
                                        ? 'border-red-200/80'
                                        : 'border-secondary-200/80'
                                    )}
                                  >
                                    <button
                                      type="button"
                                      onClick={() => toggleTool(tool.id)}
                                      className="w-full flex items-center gap-3 p-3 text-left transition-colors hover:bg-slate-50"
                                    >
                                      <div
                                        className={cn(
                                          'flex h-8 w-8 items-center justify-center rounded-lg',
                                          tool.status === 'error' ? 'bg-red-50' : 'bg-secondary-50'
                                        )}
                                      >
                                        {tool.status === 'error' ? (
                                          <Warning
                                            size={14}
                                            weight="fill"
                                            className="text-red-600"
                                          />
                                        ) : (
                                          <CheckCircle
                                            size={14}
                                            weight="fill"
                                            className="text-secondary-600"
                                          />
                                        )}
                                      </div>
                                      <div className="flex-1">
                                        <div className="flex items-center gap-2">
                                          <Wrench size={12} className="text-slate-400" />
                                          <span className="text-xs font-semibold text-slate-900 font-mono">
                                            {tool.name}
                                          </span>
                                        </div>
                                      </div>
                                      {isExpanded ? (
                                        <CaretUp size={16} className="text-slate-400" />
                                      ) : (
                                        <CaretDown size={16} className="text-slate-400" />
                                      )}
                                    </button>
                                    {isExpanded && (
                                      <div className="border-t-2 border-slate-100 bg-slate-50/50 p-3 space-y-3">
                                        {tool.args && (
                                          <pre className="text-xs text-slate-700 font-mono bg-white rounded-lg p-2 border border-slate-200 overflow-x-auto">
                                            {JSON.stringify(tool.args, null, 2)}
                                          </pre>
                                        )}
                                        {tool.output != null && (
                                          <pre className="text-xs text-slate-700 font-mono bg-white rounded-lg p-2 border border-slate-200 overflow-x-auto">
                                            {typeof tool.output === 'string'
                                              ? tool.output
                                              : JSON.stringify(tool.output, null, 2)}
                                          </pre>
                                        )}
                                        {tool.error && (
                                          <p className="text-xs text-red-600">{tool.error}</p>
                                        )}
                                      </div>
                                    )}
                                  </motion.div>
                                );
                              })}
                            </div>
                          )}
                        </div>
                      </div>
                    </motion.div>
                  ))}
                </div>

                {showRaw && (
                  <motion.div variants={itemVariants} className="mt-10">
                    <h2 className="text-lg font-semibold tracking-tight text-slate-900 mb-4">
                      Raw Wire Events
                    </h2>
                    <div className="space-y-2">
                      {events.map((ev) => (
                        <div
                          key={`${ev.seq}-${ev.event_id}`}
                          className="rounded-xl border border-slate-200 bg-white p-3 font-mono text-xs text-slate-700"
                        >
                          <span className="text-slate-400">#{ev.seq}</span>{' '}
                          <span className="font-semibold text-primary-700">{ev.type}</span>{' '}
                          <span className="text-slate-400">{ev.event_id}</span>
                          <pre className="mt-1 overflow-x-auto text-slate-600">
                            {JSON.stringify(ev.data, null, 2)}
                          </pre>
                        </div>
                      ))}
                    </div>
                  </motion.div>
                )}
              </>
            )}
          </motion.div>
        </main>
      </div>
    </AuthGuard>
  );
}
