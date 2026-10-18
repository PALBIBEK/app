/**
 * Subagents list — live BFF children via sessionsAPI (D12 B). No mock theater.
 */

'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { motion } from 'framer-motion';
import {
  GitBranch,
  Plus,
  MagnifyingGlass,
  SpinnerGap,
  WarningCircle,
  ArrowRight,
  Cube,
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

function SubagentsListContent(): JSX.Element {
  const { data: sessionsData, isLoading: sessionsLoading, error: sessionsError } =
    useSessions({ page: 1, page_size: 50 });
  const sessions = sessionsData?.data ?? [];
  const [selectedSessionId, setSelectedSessionId] = useState('');
  const [children, setChildren] = useState<ChildRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');

  useEffect(() => {
    if (!selectedSessionId && sessions.length > 0) {
      setSelectedSessionId(sessions[0].id);
    }
  }, [sessions, selectedSessionId]);

  const loadChildren = useCallback(async (sessionId: string) => {
    if (!sessionId) {
      setChildren([]);
      setLoadError(null);
      return;
    }
    setLoading(true);
    setLoadError(null);
    try {
      const rows = await sessionsAPI.listChildren(sessionId);
      setChildren(rows);
    } catch (err) {
      setChildren([]);
      setLoadError(
        err instanceof Error
          ? err.message
          : 'Could not load children from the BFF'
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadChildren(selectedSessionId);
  }, [selectedSessionId, loadChildren]);

  const filtered = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return children;
    return children.filter((c) => {
      const label = (c.label || c.id).toLowerCase();
      const provider = displaySubagentProvider(c.provider).label.toLowerCase();
      return label.includes(q) || provider.includes(q) || c.mode.includes(q);
    });
  }, [children, searchQuery]);

  return (
    <div className="grid min-h-[100dvh] grid-cols-[auto_1fr] grid-rows-[auto_1fr] bg-gradient-subtle">
      <div className="col-span-2">
        <Header />
      </div>
      <Sidebar />

      <main className="overflow-auto">
        <motion.div
          className="mx-auto max-w-5xl px-8 py-8"
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.35 }}
        >
          <div className="flex items-start justify-between gap-4">
            <div className="flex items-start gap-4">
              <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-primary-50">
                <GitBranch size={26} weight="fill" className="text-primary-600" />
              </div>
              <div>
                <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
                  Subagents
                </h1>
                <p className="mt-1 text-sm text-slate-500">
                  Live children for a parent session via BFF (not mock data)
                </p>
              </div>
            </div>
            <Link
              href="/subagents/spawn"
              className="inline-flex items-center gap-2 rounded-xl bg-primary-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-primary-700"
            >
              <Plus size={16} weight="bold" />
              Spawn Subagent
            </Link>
          </div>

          <div className="mt-6 flex flex-wrap items-center gap-3">
            <label className="text-sm font-medium text-slate-600">Parent session</label>
            <select
              value={selectedSessionId}
              onChange={(e) => setSelectedSessionId(e.target.value)}
              disabled={sessionsLoading || sessions.length === 0}
              className="h-10 min-w-[240px] rounded-xl border border-slate-200 bg-white px-3 text-sm outline-none focus:border-primary-300 focus:ring-2 focus:ring-primary-100"
            >
              {sessions.length === 0 && <option value="">No sessions</option>}
              {sessions.map((s) => (
                <option key={s.id} value={s.id}>
                  {(s.title || s.harness_id).slice(0, 40)} ({s.id.slice(0, 8)}…)
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={() => void loadChildren(selectedSessionId)}
              className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
            >
              Refresh
            </button>
          </div>

          <div className="relative mt-4 max-w-md">
            <MagnifyingGlass
              size={16}
              className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
            />
            <input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Filter children…"
              className="w-full rounded-xl border border-slate-200 bg-white py-2.5 pl-9 pr-3 text-sm outline-none focus:border-primary-300 focus:ring-2 focus:ring-primary-100"
            />
          </div>

          {(sessionsError || loadError) && (
            <div
              className="mt-6 flex items-start gap-3 rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900"
              role="alert"
            >
              <WarningCircle size={20} className="shrink-0" />
              <div>
                <p className="font-medium">Could not load subagents from the BFF</p>
                <p className="mt-1 text-amber-800/90">
                  {loadError ||
                    (sessionsError instanceof Error
                      ? sessionsError.message
                      : 'Sessions list failed')}
                  . Empty list is honest when the AI plane has no children; fake rows are never
                  shown.
                </p>
              </div>
            </div>
          )}

          {(sessionsLoading || loading) && (
            <div className="mt-10 flex items-center gap-2 text-sm text-slate-500">
              <SpinnerGap size={18} className="animate-spin" />
              Loading children…
            </div>
          )}

          {!sessionsLoading && !loading && !loadError && filtered.length === 0 && (
            <div className="mt-12 flex flex-col items-center justify-center text-center">
              <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-slate-100">
                <GitBranch size={28} className="text-slate-400" />
              </div>
              <p className="mt-4 text-sm font-medium text-slate-700">No children yet</p>
              <p className="mt-1 max-w-sm text-sm text-slate-500">
                {sessions.length === 0
                  ? 'Create a Test session first, then spawn a child.'
                  : 'Spawn a lab_echo child from this session, or wait for the agent to spawn one.'}
              </p>
              <Link
                href="/subagents/spawn"
                className="mt-4 text-sm font-medium text-primary-600 hover:text-primary-700"
              >
                Spawn Subagent →
              </Link>
            </div>
          )}

          <div className="mt-6 space-y-3">
            {filtered.map((child) => {
              const provider = displaySubagentProvider(child.provider);
              const parentId = child.session_id || selectedSessionId;
              return (
                <Link
                  key={`${parentId}-${child.id}-${child.run_id}`}
                  href={`/subagents/${encodeURIComponent(child.id)}?session=${encodeURIComponent(parentId)}`}
                  className="block"
                >
                  <div
                    className={cn(
                      'group flex items-center gap-4 rounded-2xl border border-slate-200 bg-white p-4',
                      'transition-all hover:border-slate-300 hover:shadow-md'
                    )}
                  >
                    <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-primary-50">
                      <Cube size={18} className="text-primary-600" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="truncate text-sm font-semibold text-slate-900">
                          {child.label || child.id}
                        </p>
                        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-semibold capitalize text-slate-600">
                          {child.mode.replace(/_/g, ' ')}
                        </span>
                        <span
                          className={cn(
                            'rounded-full px-2 py-0.5 text-[11px] font-semibold',
                            provider.isLab
                              ? 'bg-amber-50 text-amber-800 ring-1 ring-inset ring-amber-200'
                              : 'bg-slate-50 text-slate-700 ring-1 ring-inset ring-slate-200'
                          )}
                          title={
                            provider.isLab
                              ? 'In-process echo stub (Lab) — not durable memory'
                              : undefined
                          }
                        >
                          {provider.label}
                          {provider.isLab ? ' · Lab' : ''}
                        </span>
                        <span className="rounded-full bg-slate-50 px-2 py-0.5 text-[11px] font-medium capitalize text-slate-500">
                          {child.activity}
                        </span>
                      </div>
                      <p className="mt-1 truncate font-mono text-xs text-slate-400">
                        {child.id}
                        {child.run_id ? ` · run ${child.run_id}` : ''}
                      </p>
                    </div>
                    <ArrowRight
                      size={18}
                      className="text-slate-300 group-hover:text-slate-500"
                    />
                  </div>
                </Link>
              );
            })}
          </div>
        </motion.div>
      </main>
    </div>
  );
}

export default function SubagentsPage(): JSX.Element {
  return (
    <AuthGuard>
      <SubagentsListContent />
    </AuthGuard>
  );
}
