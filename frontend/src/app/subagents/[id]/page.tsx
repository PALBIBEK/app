/**
 * Subagent detail — live BFF listChildren lookup (D12 B). No mock theater.
 */

'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useParams, useSearchParams } from 'next/navigation';
import { motion } from 'framer-motion';
import {
  ArrowLeft,
  GitBranch,
  SpinnerGap,
  WarningCircle,
  Info,
} from '@phosphor-icons/react';
import { AuthGuard } from '@/components/auth/AuthGuard';
import { Header } from '@/components/layout/Header';
import { Sidebar } from '@/components/layout/Sidebar';
import { sessionsAPI } from '@/lib/api/endpoints/sessions';
import { useSessions } from '@/lib/api/hooks/useSessions';
import { displaySubagentProvider } from '@/lib/session/wireEventReducer';
import { cn } from '@/lib/utils';

interface ChildRow {
  id: string;
  mode: string;
  provider: string;
  parent_id: string;
  run_id: string;
  label?: string | null;
  activity: string;
  depth?: number | null;
  session_id?: string | null;
}

function SubagentDetailContent(): JSX.Element {
  const params = useParams();
  const search = useSearchParams();
  const childId = typeof params.id === 'string' ? decodeURIComponent(params.id) : '';
  const sessionFromQuery = search.get('session') || '';

  const { data: sessionsData } = useSessions({ page: 1, page_size: 50 });
  const sessions = useMemo(() => sessionsData?.data ?? [], [sessionsData]);

  const [child, setChild] = useState<ChildRow | null>(null);
  const [parentSessionId, setParentSessionId] = useState(sessionFromQuery);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const resolveChild = useCallback(async () => {
    if (!childId) {
      setLoading(false);
      setLoadError('Missing child id');
      return;
    }
    setLoading(true);
    setLoadError(null);
    try {
      const tryIds = sessionFromQuery
        ? [sessionFromQuery]
        : sessions.map((s) => s.id);

      if (tryIds.length === 0) {
        setChild(null);
        setLoadError(
          sessionFromQuery
            ? null
            : 'No parent session in query and no sessions loaded yet'
        );
        return;
      }

      for (const sid of tryIds) {
        const rows = await sessionsAPI.listChildren(sid);
        const found = rows.find((r) => r.id === childId);
        if (found) {
          setChild(found);
          setParentSessionId(found.session_id || sid);
          return;
        }
      }
      setChild(null);
      setLoadError(
        'Child not found in live children list for the selected session(s). Empty / missing is honest — no fake detail.'
      );
    } catch (err) {
      setChild(null);
      setLoadError(
        err instanceof Error ? err.message : 'Could not load child from the BFF'
      );
    } finally {
      setLoading(false);
    }
  }, [childId, sessionFromQuery, sessions]);

  useEffect(() => {
    void resolveChild();
  }, [resolveChild]);

  const provider = displaySubagentProvider(child?.provider || 'lab_echo');

  return (
    <div className="grid min-h-[100dvh] grid-cols-[auto_1fr] grid-rows-[auto_1fr] bg-gradient-subtle">
      <div className="col-span-2">
        <Header />
      </div>
      <Sidebar />

      <main className="overflow-auto">
        <motion.div
          className="mx-auto max-w-3xl px-8 py-8"
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
        >
          <Link
            href="/subagents"
            className="inline-flex items-center gap-2 text-sm font-medium text-slate-500 hover:text-slate-700"
          >
            <ArrowLeft size={16} />
            Back to Subagents
          </Link>

          {loading && (
            <div className="mt-10 flex items-center gap-2 text-sm text-slate-500">
              <SpinnerGap size={18} className="animate-spin" />
              Loading child via BFF…
            </div>
          )}

          {loadError && (
            <div
              className="mt-6 flex items-start gap-3 rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900"
              role="alert"
            >
              <WarningCircle size={20} className="shrink-0" />
              <div>
                <p className="font-medium">Subagent detail unavailable</p>
                <p className="mt-1">{loadError}</p>
              </div>
            </div>
          )}

          {!loading && child && (
            <>
              <div className="mt-6 flex items-start gap-4">
                <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-primary-50">
                  <GitBranch size={26} weight="fill" className="text-primary-600" />
                </div>
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
                      {child.label || child.id}
                    </h1>
                    <span
                      className={cn(
                        'rounded-full px-2.5 py-1 text-xs font-semibold',
                        provider.isLab
                          ? 'bg-amber-50 text-amber-800 ring-1 ring-inset ring-amber-200'
                          : 'bg-slate-100 text-slate-700'
                      )}
                    >
                      {provider.label}
                      {provider.isLab ? ' · Lab' : ''}
                    </span>
                    <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium capitalize text-slate-600">
                      {child.activity}
                    </span>
                  </div>
                  <p className="mt-1 font-mono text-xs text-slate-500">{child.id}</p>
                </div>
              </div>

              <div className="mt-8 rounded-2xl border border-slate-200 bg-white p-6">
                <h2 className="text-sm font-semibold text-slate-800">Live child row</h2>
                <dl className="mt-4 grid grid-cols-2 gap-4 text-sm">
                  <div>
                    <dt className="text-slate-500">Mode</dt>
                    <dd className="mt-1 font-medium capitalize text-slate-900">
                      {child.mode.replace(/_/g, ' ')}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-slate-500">Provider</dt>
                    <dd className="mt-1 font-mono text-slate-900">{provider.label}</dd>
                  </div>
                  <div>
                    <dt className="text-slate-500">Run id</dt>
                    <dd className="mt-1 break-all font-mono text-xs text-slate-900">
                      {child.run_id || '—'}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-slate-500">Depth</dt>
                    <dd className="mt-1 text-slate-900">
                      {child.depth != null ? child.depth : '—'}
                    </dd>
                  </div>
                  <div className="col-span-2">
                    <dt className="text-slate-500">Parent product session</dt>
                    <dd className="mt-1 break-all font-mono text-xs text-slate-900">
                      {parentSessionId || '—'}
                    </dd>
                  </div>
                  <div className="col-span-2">
                    <dt className="text-slate-500">Supervisor parent id</dt>
                    <dd className="mt-1 break-all font-mono text-xs text-slate-900">
                      {child.parent_id}
                    </dd>
                  </div>
                </dl>
              </div>

              {provider.isLab && (
                <div className="mt-4 flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
                  <Info size={18} className="mt-0.5 shrink-0" />
                  <p>
                    <span className="font-semibold">lab_echo · Lab</span> is an in-process
                    echo transport stub for local demo. It is not Durable Memory and not a
                    production nested agent runtime.
                  </p>
                </div>
              )}

              {parentSessionId && (
                <div className="mt-6 flex gap-4 text-sm">
                  <Link
                    href={`/sessions/${parentSessionId}`}
                    className="font-medium text-primary-600 hover:text-primary-700"
                  >
                    Open parent session traces →
                  </Link>
                  <Link
                    href="/subagents/spawn"
                    className="font-medium text-slate-600 hover:text-slate-800"
                  >
                    Spawn another →
                  </Link>
                </div>
              )}
            </>
          )}
        </motion.div>
      </main>
    </div>
  );
}

export default function SubagentDetailPage(): JSX.Element {
  return (
    <AuthGuard>
      <SubagentDetailContent />
    </AuthGuard>
  );
}
