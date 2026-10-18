/**
 * Trace Detail — session journal timeline first (LOCAL_DEMO #7 / D4).
 * Span waterfall (D13 Trace API) is a secondary tab when spans exist.
 */

'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { motion } from 'framer-motion';
import {
  ArrowLeft,
  ChartLine,
  CircleNotch,
  Clock,
  TreeStructure,
} from '@phosphor-icons/react';
import { AuthGuard } from '@/components/auth/AuthGuard';
import { Header } from '@/components/layout/Header';
import { Sidebar } from '@/components/layout/Sidebar';
import { tracesAPI, type TraceSpan } from '@/lib/api/endpoints/traces';
import { sessionsAPI } from '@/lib/api/endpoints/sessions';
import { useSession } from '@/lib/api/hooks/useSessions';
import type { WireEvent } from '@/lib/api/types/session.types';
import {
  applyWireEvent,
  createSessionViewState,
  type SessionViewState,
} from '@/lib/session/wireEventReducer';
import { cn } from '@/lib/utils';

const pageVariants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { staggerChildren: 0.06 } },
};

const itemVariants = {
  hidden: { opacity: 0, y: 12 },
  visible: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.35, ease: [0.25, 0.46, 0.45, 0.94] },
  },
};

type TreeNode = {
  span: TraceSpan;
  children: TreeNode[];
  depth: number;
};

type DetailTab = 'journal' | 'waterfall';

function spanIdOf(span: TraceSpan): string {
  return String(span.spanID ?? span.span_id ?? '');
}

function parentIdOf(span: TraceSpan): string | null {
  const refs = span.references;
  if (Array.isArray(refs)) {
    for (const r of refs) {
      if (
        r &&
        typeof r === 'object' &&
        (r.refType === 'CHILD_OF' || r.ref_type === 'CHILD_OF') &&
        (r.spanID || r.span_id)
      ) {
        return String(r.spanID ?? r.span_id);
      }
    }
  }
  if (span.parentSpanId || span.parent_span_id) {
    return String(span.parentSpanId ?? span.parent_span_id);
  }
  return null;
}

function operationOf(span: TraceSpan): string {
  return String(
    span.operationName ?? span.operation_name ?? span.name ?? 'span'
  );
}

function durationUs(span: TraceSpan): number | null {
  const d = span.duration ?? span.duration_us;
  return typeof d === 'number' ? d : null;
}

function buildTree(spans: TraceSpan[]): TreeNode[] {
  const byId = new Map<string, TreeNode>();
  for (const span of spans) {
    const id = spanIdOf(span);
    if (!id) continue;
    byId.set(id, { span, children: [], depth: 0 });
  }
  const roots: TreeNode[] = [];
  for (const node of byId.values()) {
    const pid = parentIdOf(node.span);
    if (pid && byId.has(pid)) {
      byId.get(pid)!.children.push(node);
    } else {
      roots.push(node);
    }
  }
  const assignDepth = (nodes: TreeNode[], depth: number) => {
    for (const n of nodes) {
      n.depth = depth;
      assignDepth(n.children, depth + 1);
    }
  };
  assignDepth(roots, 0);
  return roots;
}

function flattenTree(nodes: TreeNode[]): TreeNode[] {
  const out: TreeNode[] = [];
  const walk = (list: TreeNode[]) => {
    for (const n of list) {
      out.push(n);
      walk(n.children);
    }
  };
  walk(nodes);
  return out;
}

export default function TraceDetailPage(): JSX.Element {
  const params = useParams();
  const id = typeof params.id === 'string' ? params.id : '';
  const { data: session } = useSession(id);

  const [tab, setTab] = useState<DetailTab>('journal');
  const [spans, setSpans] = useState<TraceSpan[] | null>(null);
  const [spansLoading, setSpansLoading] = useState(true);
  const [spansError, setSpansError] = useState<string | null>(null);

  const [viewState, setViewState] = useState<SessionViewState>(() =>
    createSessionViewState()
  );
  const [events, setEvents] = useState<WireEvent[]>([]);
  const [journalLoading, setJournalLoading] = useState(true);
  const [journalError, setJournalError] = useState<string | null>(null);

  // Primary: session journal timeline (D4 / LOCAL_DEMO #7).
  useEffect(() => {
    if (!id) {
      setJournalLoading(false);
      return;
    }
    const ac = new AbortController();
    let cancelled = false;
    setViewState(createSessionViewState());
    setEvents([]);
    setJournalError(null);
    setJournalLoading(true);

    (async () => {
      try {
        for await (const event of sessionsAPI.openEventStream(id, {
          signal: ac.signal,
        })) {
          if (cancelled) break;
          setEvents((prev) => [...prev, event]);
          setViewState((prev) => applyWireEvent(prev, event));
          setJournalLoading(false);
        }
        if (!cancelled) setJournalLoading(false);
      } catch (err) {
        if (cancelled || (err instanceof DOMException && err.name === 'AbortError')) {
          return;
        }
        if (!cancelled) {
          setJournalError(
            err instanceof Error ? err.message : 'Failed to load session journal'
          );
          setJournalLoading(false);
        }
      }
    })();

    return () => {
      cancelled = true;
      ac.abort();
    };
  }, [id]);

  // Secondary: Trace API span waterfall (D13).
  useEffect(() => {
    if (!id) {
      setSpansLoading(false);
      setSpans([]);
      return;
    }
    let cancelled = false;
    setSpansLoading(true);
    setSpansError(null);
    (async () => {
      try {
        const result = await tracesAPI.getSpans(id);
        if (!cancelled) {
          setSpans(result.spans);
        }
      } catch (err) {
        if (!cancelled) {
          const status =
            err && typeof err === 'object' && 'response' in err
              ? (err as { response?: { status?: number } }).response?.status
              : undefined;
          if (status === 404) {
            setSpans([]);
            setSpansError(null);
          } else {
            setSpans([]);
            setSpansError(
              err instanceof Error ? err.message : 'Failed to load span tree'
            );
          }
        }
      } finally {
        if (!cancelled) setSpansLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id]);

  const rows = useMemo(() => {
    if (!spans || spans.length === 0) return [];
    return flattenTree(buildTree(spans));
  }, [spans]);

  const maxDuration = useMemo(() => {
    let max = 1;
    for (const n of rows) {
      const d = durationUs(n.span);
      if (d != null && d > max) max = d;
    }
    return max;
  }, [rows]);

  const orderedEvents = useMemo(() => {
    return [...events].sort((a, b) => {
      const sa = typeof a.seq === 'number' ? a.seq : 0;
      const sb = typeof b.seq === 'number' ? b.seq : 0;
      return sa - sb;
    });
  }, [events]);

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
            <motion.div variants={itemVariants} className="flex items-start justify-between gap-4">
              <div className="flex items-start gap-4">
                <Link
                  href="/traces"
                  className="mt-1 flex h-10 w-10 items-center justify-center rounded-xl border-2 border-slate-200 bg-white text-slate-600 hover:border-slate-300"
                  aria-label="Back to traces"
                >
                  <ArrowLeft size={18} />
                </Link>
                <div>
                  <div className="flex items-center gap-3">
                    <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
                      Session journal timeline
                    </h1>
                    <span className="rounded-lg bg-secondary-50 px-2.5 py-1 text-xs font-semibold text-secondary-700 ring-1 ring-inset ring-secondary-200">
                      Journal
                    </span>
                  </div>
                  <p className="mt-1 font-mono text-xs text-slate-500">{id || '—'}</p>
                  {session?.status ? (
                    <p className="mt-1 text-xs capitalize text-slate-500">
                      {String(session.status).replace(/_/g, ' ')}
                    </p>
                  ) : null}
                </div>
              </div>
              {id ? (
                <Link
                  href={`/sessions/${id}`}
                  className={cn(
                    'inline-flex items-center gap-2 rounded-xl px-4 py-2.5',
                    'text-sm font-medium text-slate-700',
                    'border-2 border-slate-200 bg-white',
                    'hover:border-slate-300 hover:shadow-sm'
                  )}
                >
                  <ChartLine size={16} />
                  Session timeline
                </Link>
              ) : null}
            </motion.div>

            <motion.div variants={itemVariants} className="mt-6 flex gap-2">
              <button
                type="button"
                onClick={() => setTab('journal')}
                className={cn(
                  'rounded-xl px-4 py-2 text-sm font-medium',
                  tab === 'journal'
                    ? 'bg-slate-900 text-white'
                    : 'border border-slate-200 bg-white text-slate-600 hover:border-slate-300'
                )}
              >
                Journal
              </button>
              <button
                type="button"
                onClick={() => setTab('waterfall')}
                className={cn(
                  'rounded-xl px-4 py-2 text-sm font-medium',
                  tab === 'waterfall'
                    ? 'bg-slate-900 text-white'
                    : 'border border-slate-200 bg-white text-slate-600 hover:border-slate-300'
                )}
              >
                Span waterfall
                {spans && spans.length > 0 ? (
                  <span className="ml-1.5 text-xs opacity-70">({spans.length})</span>
                ) : null}
              </button>
            </motion.div>

            {tab === 'journal' && (
              <>
                {journalLoading && events.length === 0 && (
                  <motion.p
                    variants={itemVariants}
                    className="mt-10 flex items-center gap-2 text-sm text-slate-500"
                  >
                    <CircleNotch size={16} className="animate-spin text-primary-500" />
                    Loading session journal…
                  </motion.p>
                )}
                {journalError && (
                  <motion.p variants={itemVariants} className="mt-10 text-sm text-red-600">
                    {journalError}
                  </motion.p>
                )}
                {!journalLoading && !journalError && orderedEvents.length === 0 && (
                  <motion.div
                    variants={itemVariants}
                    className="mt-10 rounded-2xl border-2 border-dashed border-slate-200 bg-white/80 px-6 py-10 text-center"
                  >
                    <p className="text-sm font-medium text-slate-800">No journal events yet</p>
                    <p className="mt-1 text-sm text-slate-500">
                      Run a Test chat for this session, then reopen Traces. Full session
                      timeline remains on the dedicated page.
                    </p>
                    {id ? (
                      <Link
                        href={`/sessions/${id}`}
                        className="mt-5 inline-flex text-sm font-medium text-primary-600 hover:underline"
                      >
                        Open session timeline
                      </Link>
                    ) : null}
                  </motion.div>
                )}
                {orderedEvents.length > 0 && (
                  <motion.ol variants={itemVariants} className="mt-8 space-y-2">
                    {orderedEvents.map((ev, idx) => (
                      <li
                        key={`${ev.type}-${ev.seq ?? idx}-${idx}`}
                        className="rounded-xl border border-slate-200/80 bg-white px-4 py-3 shadow-sm"
                      >
                        <div className="flex items-center justify-between gap-3">
                          <p className="text-sm font-semibold text-slate-900">{ev.type}</p>
                          <span className="font-mono text-[11px] text-slate-400">
                            seq {ev.seq ?? '—'}
                          </span>
                        </div>
                        {viewState.messages.length > 0 && ev.type === 'message_end' ? (
                          <p className="mt-1 truncate text-xs text-slate-500">
                            message settled
                          </p>
                        ) : null}
                      </li>
                    ))}
                  </motion.ol>
                )}
              </>
            )}

            {tab === 'waterfall' && (
              <>
                {spansLoading && (
                  <motion.p
                    variants={itemVariants}
                    className="mt-10 flex items-center gap-2 text-sm text-slate-500"
                  >
                    <CircleNotch size={16} className="animate-spin text-primary-500" />
                    Loading spans…
                  </motion.p>
                )}

                {!spansLoading && spansError && (
                  <motion.p variants={itemVariants} className="mt-10 text-sm text-red-600">
                    {spansError}
                  </motion.p>
                )}

                {!spansLoading && !spansError && spans !== null && spans.length === 0 && (
                  <motion.div
                    variants={itemVariants}
                    className="mt-10 rounded-2xl border-2 border-dashed border-slate-200 bg-white/80 px-6 py-10 text-center"
                  >
                    <TreeStructure size={28} className="mx-auto text-slate-400" />
                    <p className="mt-3 text-sm font-medium text-slate-800">
                      No spans for this id
                    </p>
                    <p className="mt-1 text-sm text-slate-500">
                      Jaeger/Trace API returned no tenant-scoped spans. Session journal
                      timeline remains available.
                    </p>
                    {id ? (
                      <Link
                        href={`/sessions/${id}`}
                        className="mt-5 inline-flex text-sm font-medium text-primary-600 hover:underline"
                      >
                        Open session timeline
                      </Link>
                    ) : null}
                  </motion.div>
                )}

                {!spansLoading && rows.length > 0 && (
                  <motion.div variants={itemVariants} className="mt-8 space-y-2">
                    {rows.map((node) => {
                      const dur = durationUs(node.span);
                      const widthPct =
                        dur != null
                          ? Math.max(4, Math.round((dur / maxDuration) * 100))
                          : 8;
                      return (
                        <div
                          key={spanIdOf(node.span) || operationOf(node.span)}
                          className="rounded-xl border border-slate-200/80 bg-white px-4 py-3 shadow-sm"
                          style={{ marginLeft: node.depth * 16 }}
                        >
                          <div className="flex items-center justify-between gap-3">
                            <div className="min-w-0">
                              <p className="truncate text-sm font-semibold text-slate-900">
                                {operationOf(node.span)}
                              </p>
                              <p className="mt-0.5 font-mono text-[11px] text-slate-500">
                                {spanIdOf(node.span) || '—'}
                              </p>
                            </div>
                            <span className="flex shrink-0 items-center gap-1 text-xs text-slate-500">
                              <Clock size={12} />
                              {dur != null ? `${(dur / 1000).toFixed(1)} ms` : '—'}
                            </span>
                          </div>
                          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-100">
                            <div
                              className="h-full rounded-full bg-primary-500/80"
                              style={{ width: `${widthPct}%` }}
                            />
                          </div>
                        </div>
                      );
                    })}
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
