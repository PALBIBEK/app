/**
 * Spawn Subagent — live BFF sessionsAPI.spawnSubagent (D12 B).
 * Default provider: lab_echo (Lab echo stub — not durable memory).
 */

'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { motion } from 'framer-motion';
import {
  ArrowLeft,
  SpinnerGap,
  WarningCircle,
  CaretRight,
  Sparkle,
} from '@phosphor-icons/react';
import { AuthGuard } from '@/components/auth/AuthGuard';
import { Header } from '@/components/layout/Header';
import { Sidebar } from '@/components/layout/Sidebar';
import { sessionsAPI } from '@/lib/api/endpoints/sessions';
import { useHarnesses } from '@/lib/api/hooks/useHarnesses';
import { useSessions } from '@/lib/api/hooks/useSessions';
import { cn } from '@/lib/utils';
import { toast } from 'sonner';

/** Product UI: one-shot spawn only (A4). Other modes are not wired on BFF. */
const PRODUCT_SPAWN_MODE = 'spawn' as const;


/** Product providers — lab_echo is the echo stub (supervisor maps to memory). */
const providers = [
  {
    id: 'lab_echo',
    name: 'lab_echo',
    badge: 'Lab',
    hint: 'In-process echo stub for local demo — not Durable Memory',
  },
  {
    id: 'acp',
    name: 'acp',
    badge: null,
    hint: 'ACP remote provider when supervisor has ACP configured',
  },
];

function SpawnSubagentContent(): JSX.Element {
  const router = useRouter();
  const search = useSearchParams();
  const sessionFromQuery = search.get('session') || '';
  const { data: sessionsData, isLoading: sessionsLoading, error: sessionsError } =
    useSessions({ page: 1, page_size: 50 });
  const sessions = sessionsData?.data ?? [];
  const { data: harnessList } = useHarnesses();
  const harnessNames = new Map((harnessList?.data ?? []).map((h) => [h.id, h.name]));

  const selectedMode = PRODUCT_SPAWN_MODE;
  const [selectedProvider, setSelectedProvider] = useState('lab_echo');
  const [selectedParent, setSelectedParent] = useState(sessionFromQuery);
  const [label, setLabel] = useState('');
  const [prompt, setPrompt] = useState('');
  const [depthLimit, setDepthLimit] = useState('3');
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  useEffect(() => {
    if (sessionFromQuery) {
      setSelectedParent(sessionFromQuery);
      return;
    }
    if (!selectedParent && sessions.length > 0) {
      setSelectedParent(sessions[0].id);
    }
  }, [sessions, selectedParent, sessionFromQuery]);

  const onSpawn = async () => {
    setSubmitError(null);
    if (!selectedParent) {
      setSubmitError('Select a parent session (create one from Test chat first).');
      return;
    }
    if (!prompt.trim()) {
      setSubmitError('Task prompt is required.');
      return;
    }
    setSubmitting(true);
    try {
      const result = await sessionsAPI.spawnSubagent(selectedParent, {
        prompt: prompt.trim(),
        description: label.trim() || undefined,
        provider: selectedProvider,
        mode: selectedMode,
        depth_limit: Number(depthLimit) || undefined,
      });
      const childId = result.child_session_id;
      toast.success(childId ? `Spawned ${childId}` : 'Subagent spawn accepted');
      if (childId) {
        router.push(
          `/subagents/${encodeURIComponent(childId)}?session=${encodeURIComponent(selectedParent)}`
        );
      } else {
        router.push('/subagents');
      }
    } catch (err) {
      const msg =
        err instanceof Error ? err.message : 'Spawn failed via BFF';
      setSubmitError(msg);
      toast.error(msg);
    } finally {
      setSubmitting(false);
    }
  };

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

          <div className="mt-6 flex items-start gap-4">
            <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-primary-50">
              <Sparkle size={26} weight="fill" className="text-primary-600" />
            </div>
            <div>
              <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
                Spawn Subagent
              </h1>
              <p className="mt-1 text-sm text-slate-500">
                Creates a child via product BFF → supervisor (service JWT). Default provider is
                lab_echo (Lab).
              </p>
            </div>
          </div>

          {(sessionsError || submitError) && (
            <div
              className="mt-6 flex items-start gap-3 rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900"
              role="alert"
            >
              <WarningCircle size={20} className="shrink-0" />
              <div>
                <p className="font-medium">
                  {submitError ? 'Spawn failed' : 'Could not load parent sessions'}
                </p>
                <p className="mt-1">
                  {submitError ||
                    (sessionsError instanceof Error
                      ? sessionsError.message
                      : 'Sessions BFF error')}
                </p>
              </div>
            </div>
          )}

          <div className="mt-8 space-y-8">
            <section>
              <h2 className="text-sm font-semibold text-slate-800">Execution mode</h2>
              <p className="mt-2 text-sm text-slate-600">
                One-shot spawn only (<code className="text-xs">PRODUCT_SPAWN_MODE=spawn</code>).
                Fork, background, and continuable are not offered in product UI.
              </p>
            </section>

            <section>
              <h2 className="text-sm font-semibold text-slate-800">Parent session</h2>
              <select
                value={selectedParent}
                onChange={(e) => setSelectedParent(e.target.value)}
                disabled={sessionsLoading || sessions.length === 0}
                className="mt-3 h-11 w-full rounded-xl border border-slate-200 bg-white px-4 text-sm"
              >
                {sessions.length === 0 && (
                  <option value="">No sessions — open Test chat first</option>
                )}
                {sessions.map((s) => (
                  <option key={s.id} value={s.id}>
                    {(s.title || harnessNames.get(s.harness_id) || 'Session').slice(0, 48)} · {s.id}
                  </option>
                ))}
              </select>
            </section>

            <section>
              <h2 className="text-sm font-semibold text-slate-800">Provider</h2>
              <p className="mt-1 text-xs text-slate-500">
                lab_echo is the Lab echo stub. Never confuse it with Durable Memory.
              </p>
              <div className="mt-3 space-y-2">
                {providers.map((p) => {
                  const selected = selectedProvider === p.id;
                  return (
                    <button
                      key={p.id}
                      type="button"
                      onClick={() => setSelectedProvider(p.id)}
                      className={cn(
                        'flex w-full items-start gap-3 rounded-xl border p-3 text-left',
                        selected
                          ? 'border-primary-400 bg-primary-50'
                          : 'border-slate-200 bg-white'
                      )}
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-sm font-semibold text-slate-900">
                            {p.name}
                          </span>
                          {p.badge && (
                            <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-amber-800 ring-1 ring-inset ring-amber-200">
                              {p.badge}
                            </span>
                          )}
                        </div>
                        <p className="mt-1 text-xs text-slate-500">{p.hint}</p>
                      </div>
                    </button>
                  );
                })}
              </div>
            </section>

            <section className="space-y-4">
              <div>
                <label className="text-sm font-medium text-slate-700">Task label</label>
                <input
                  value={label}
                  onChange={(e) => setLabel(e.target.value)}
                  placeholder="Optional description"
                  className="mt-2 h-11 w-full rounded-xl border border-slate-200 px-4 text-sm"
                />
              </div>
              <div>
                <label className="text-sm font-medium text-slate-700">Task prompt</label>
                <textarea
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  rows={6}
                  placeholder="Describe the child task…"
                  className="mt-2 w-full rounded-xl border border-slate-200 px-4 py-3 text-sm"
                />
              </div>
              <div>
                <label className="text-sm font-medium text-slate-700">Depth limit</label>
                <input
                  type="number"
                  min={1}
                  max={10}
                  value={depthLimit}
                  onChange={(e) => setDepthLimit(e.target.value)}
                  className="mt-2 h-11 w-full rounded-xl border border-slate-200 px-4 text-sm"
                />
              </div>
            </section>
          </div>

          <div className="mt-8 flex items-center justify-between border-t border-slate-200 pt-6">
            <Link href="/subagents" className="text-sm text-slate-500 hover:text-slate-700">
              Cancel
            </Link>
            <button
              type="button"
              disabled={submitting || !selectedParent}
              onClick={() => void onSpawn()}
              className={cn(
                'inline-flex items-center gap-2 rounded-xl px-5 py-2.5 text-sm font-medium text-white',
                'bg-primary-600 hover:bg-primary-700 disabled:opacity-50'
              )}
            >
              {submitting ? (
                <SpinnerGap size={16} className="animate-spin" />
              ) : (
                <CaretRight size={16} weight="bold" />
              )}
              Spawn Subagent
            </button>
          </div>
        </motion.div>
      </main>
    </div>
  );
}

export default function SpawnSubagentPage(): JSX.Element {
  return (
    <AuthGuard>
      <SpawnSubagentContent />
    </AuthGuard>
  );
}
