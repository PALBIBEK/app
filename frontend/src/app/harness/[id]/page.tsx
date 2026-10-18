/**
 * Harness detail — live API data (Sprint 5).
 * Tools attach via PUT harness.tools: [uuid…] (D6 / LOCAL_DEMO #3).
 */

'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { motion } from 'framer-motion';
import {
  ArrowLeft,
  Brain,
  Cube,
  Play,
  Plus,
  SpinnerGap,
  Trash,
  WarningCircle,
  Wrench,
} from '@phosphor-icons/react';
import { Header } from '@/components/layout/Header';
import { Sidebar } from '@/components/layout/Sidebar';
import { AuthGuard } from '@/components/auth/AuthGuard';
import { AiPlatformBanner } from '@/components/platform/AiPlatformBanner';
import {
  useBindHarness,
  useDeleteHarness,
  useHarness,
  useUpdateHarness,
} from '@/lib/api/hooks/useHarnesses';
import { useTools } from '@/lib/api/hooks/useTools';
import { usePlatformStatus } from '@/lib/api/hooks/usePlatformStatus';
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

function HarnessDetailContent(): JSX.Element {
  const params = useParams();
  const router = useRouter();
  const id = typeof params.id === 'string' ? params.id : '';
  const { data: harness, isLoading, error } = useHarness(id);
  const updateHarness = useUpdateHarness();
  const deleteHarness = useDeleteHarness();
  const bindHarness = useBindHarness();
  const { data: platform, isLoading: platformLoading, isFetching } = usePlatformStatus();
  const isBound = harness?.bind?.bind_status === 'bound';
  const platformReady = !platformLoading && !isFetching && platform?.run_allowed === true;
  const testEnabled = Boolean(isBound && platformReady);
  const testTitle = !isBound
    ? 'Bind a composition before testing'
    : !platformReady
      ? 'AI platform unavailable — Test is disabled'
      : 'Test harness';

  const { data: toolsList } = useTools({ page_size: 100 });
  const ownedTools = toolsList?.data ?? [];

  const [description, setDescription] = useState('');
  const [systemPrompt, setSystemPrompt] = useState('');
  const [selectedToolIds, setSelectedToolIds] = useState<string[]>([]);
  const [manualToolUuid, setManualToolUuid] = useState('');

  useEffect(() => {
    if (!harness) {
      return;
    }
    setDescription(harness.description ?? '');
    setSystemPrompt(harness.system_prompt ?? '');
    setSelectedToolIds([...(harness.tools ?? [])]);
  }, [harness]);

  const selectedToolMeta = useMemo(() => {
    const byId = new Map(ownedTools.map((t) => [t.id, t]));
    return selectedToolIds.map((tid) => ({
      id: tid,
      name: byId.get(tid)?.name ?? tid.slice(0, 8) + '…',
      known: byId.has(tid),
    }));
  }, [ownedTools, selectedToolIds]);

  function toggleToolId(toolId: string): void {
    setSelectedToolIds((prev) =>
      prev.includes(toolId) ? prev.filter((id) => id !== toolId) : [...prev, toolId]
    );
  }

  function addManualUuid(): void {
    const tid = manualToolUuid.trim();
    if (!tid || selectedToolIds.includes(tid)) {
      return;
    }
    setSelectedToolIds((prev) => [...prev, tid]);
    setManualToolUuid('');
  }

  async function handleSave(): Promise<void> {
    if (!harness) {
      return;
    }
    await updateHarness.mutateAsync({
      id: harness.id,
      data: {
        description: description.trim() || null,
        system_prompt: systemPrompt.trim() || null,
        tools: selectedToolIds,
      },
    });
  }

  async function handleDelete(): Promise<void> {
    if (!harness) {
      return;
    }
    if (!window.confirm(`Delete harness “${harness.name}”? This cannot be undone.`)) {
      return;
    }
    await deleteHarness.mutateAsync(harness.id);
    router.push('/');
  }

  if (isLoading) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center gap-3 text-slate-500">
        <SpinnerGap size={24} className="animate-spin" />
        Loading harness…
      </div>
    );
  }

  if (error || !harness) {
    return (
      <div className="mx-auto max-w-lg rounded-2xl border border-red-200 bg-red-50/80 p-8 text-center">
        <WarningCircle size={32} className="mx-auto text-red-500" />
        <p className="mt-3 text-sm font-semibold text-red-800">
          Harness not found or you do not have access.
        </p>
        <Link
          href="/"
          className="mt-4 inline-flex text-sm font-medium text-primary-600 hover:text-primary-700"
        >
          Back to dashboard
        </Link>
      </div>
    );
  }

  return (
    <motion.div
      className="mx-auto max-w-3xl px-8 py-8"
      initial="hidden"
      animate="visible"
      variants={pageVariants}
    >
      <motion.div variants={itemVariants}>
        <Link
          href="/"
          className="group inline-flex items-center gap-2 text-sm font-medium text-slate-500 transition-colors hover:text-slate-700"
        >
          <ArrowLeft size={16} className="transition-transform group-hover:-translate-x-1" />
          Back to Harnesses
        </Link>
      </motion.div>

      <motion.div variants={itemVariants} className="mt-6 flex items-start justify-between gap-4">
        <div className="flex items-start gap-4">
        <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-secondary-400 to-secondary-600 shadow-lg shadow-secondary-500/25">
          <Cube size={26} weight="fill" className="text-white" />
        </div>
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
            {harness.name}
          </h1>
          <p className="mt-1 text-sm text-slate-500">
            {harness.model} / {harness.mode}
            {harness.tools.length > 0
              ? ` / ${harness.tools.length} tool${harness.tools.length === 1 ? '' : 's'}`
              : ''}
            {' · '}
            <span className="font-medium text-slate-600">
              {(harness.bind?.bind_status ?? 'unbound') === 'bound' ? 'Bound' : 'Draft'}
            </span>
            {harness.bind?.composition_digest ? (
              <span className="ml-2 font-mono text-xs text-slate-400">
                {harness.bind.harness_version}
                {' · '}
                {harness.bind.composition_digest.slice(0, 19)}…
              </span>
            ) : null}
          </p>
        </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => void bindHarness.mutateAsync({ id: harness.id })}
            disabled={bindHarness.isPending}
            className={cn(
              'inline-flex items-center gap-2 rounded-xl px-4 py-2.5',
              'text-sm font-medium text-slate-700',
              'border border-slate-200 bg-white hover:bg-slate-50',
              'disabled:cursor-not-allowed disabled:opacity-60'
            )}
          >
            {bindHarness.isPending
              ? 'Binding…'
              : isBound
                ? 'Re-bind (publish tools)'
                : 'Bind coding-v1'}
          </button>
          {testEnabled ? (
          <Link
            href={`/harness/${harness.id}/test`}
              title={testTitle}
            className={cn(
              'inline-flex items-center gap-2 rounded-xl px-4 py-2.5',
              'text-sm font-medium text-white',
              'bg-gradient-to-br from-primary-500 to-primary-600'
            )}
          >
            <Play size={16} weight="fill" />
            Test harness
          </Link>
        ) : (
          <button
            type="button"
            disabled
              title={testTitle}
            className="inline-flex cursor-not-allowed items-center gap-2 rounded-xl bg-slate-100 px-4 py-2.5 text-sm font-medium text-slate-400"
          >
            <Play size={16} weight="fill" />
            Test harness
          </button>
        )}
        </div>
      </motion.div>

      <motion.div variants={itemVariants} className="mt-4">
        <AiPlatformBanner />
      </motion.div>

      <motion.section
        variants={itemVariants}
        className="mt-8 space-y-4 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm"
      >
        <div>
          <label htmlFor="description" className="text-sm font-semibold text-slate-900">
            Description
          </label>
          <textarea
            id="description"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            rows={3}
            className="mt-2 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm text-slate-800 outline-none focus:border-primary-300 focus:ring-2 focus:ring-primary-100"
            placeholder="Optional description"
          />
        </div>

        <div>
          <label htmlFor="system_prompt" className="text-sm font-semibold text-slate-900">
            System prompt
          </label>
          <textarea
            id="system_prompt"
            value={systemPrompt}
            onChange={(event) => setSystemPrompt(event.target.value)}
            rows={8}
            className="mt-2 w-full rounded-xl border border-slate-200 px-3 py-2 font-mono text-sm text-slate-800 outline-none focus:border-primary-300 focus:ring-2 focus:ring-primary-100"
            placeholder="Optional system prompt"
          />
        </div>

        <div className="flex flex-wrap gap-3 pt-2">
          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={updateHarness.isPending}
            className={cn(
              'rounded-xl px-5 py-2.5 text-sm font-medium text-white',
              'bg-gradient-to-br from-primary-500 to-primary-600',
              'shadow-lg shadow-primary-500/25 disabled:opacity-60'
            )}
          >
            {updateHarness.isPending ? 'Saving…' : 'Save changes'}
          </button>
        </div>
      </motion.section>

      <motion.section
        variants={itemVariants}
        className="mt-6 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm"
        data-testid="harness-tools-attach"
      >
        <div className="flex items-start gap-4">
          <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-primary-50 text-primary-600">
            <Wrench size={22} weight="fill" />
          </div>
          <div className="min-w-0 flex-1">
            <h2 className="text-sm font-semibold text-slate-900">Attached tools</h2>
            <p className="mt-1 text-sm text-slate-500">
              Select owned tool UUIDs. Save sends{' '}
              <code className="rounded bg-slate-100 px-1 text-xs">tools: [uuid…]</code> via PUT
              harness. Re-bind after attach so the session ToolSet includes them (echo path OK
              without vendor keys).
            </p>

            {selectedToolMeta.length > 0 ? (
              <ul className="mt-3 flex flex-wrap gap-2">
                {selectedToolMeta.map((t) => (
                  <li key={t.id}>
                    <button
                      type="button"
                      onClick={() => toggleToolId(t.id)}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-primary-200 bg-primary-50 px-2.5 py-1 text-xs font-medium text-primary-800"
                      title={t.id}
                    >
                      {t.name}
                      <span className="font-mono text-[10px] text-primary-500">
                        {t.id.slice(0, 8)}…
                      </span>
                      ×
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-3 text-xs text-slate-400">No tools attached yet.</p>
            )}

            <div className="mt-4 max-h-48 space-y-2 overflow-y-auto rounded-xl border border-slate-100 bg-slate-50/80 p-3">
              {ownedTools.length === 0 ? (
                <p className="text-xs text-slate-500">
                  No owned tools yet.{' '}
                  <Link href="/tools/new" className="font-medium text-primary-600 hover:underline">
                    Create a tool
                  </Link>
                </p>
              ) : (
                ownedTools.map((tool) => {
                  const checked = selectedToolIds.includes(tool.id);
                  return (
                    <label
                      key={tool.id}
                      className={cn(
                        'flex cursor-pointer items-start gap-3 rounded-lg px-2 py-2',
                        checked ? 'bg-white ring-1 ring-primary-200' : 'hover:bg-white/80'
                      )}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleToolId(tool.id)}
                        className="mt-1 rounded border-slate-300"
                      />
                      <span className="min-w-0">
                        <span className="block text-sm font-medium text-slate-800">{tool.name}</span>
                        <span className="block font-mono text-[11px] text-slate-400">{tool.id}</span>
                      </span>
                    </label>
                  );
                })
              )}
            </div>

            <div className="mt-3 flex flex-wrap items-center gap-2">
              <input
                type="text"
                value={manualToolUuid}
                onChange={(e) => setManualToolUuid(e.target.value)}
                placeholder="Paste tool UUID…"
                className="h-9 min-w-[16rem] flex-1 rounded-lg border border-slate-200 px-3 font-mono text-xs outline-none focus:border-primary-300 focus:ring-2 focus:ring-primary-100"
              />
              <button
                type="button"
                onClick={addManualUuid}
                className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
              >
                <Plus size={12} weight="bold" />
                Add UUID
              </button>
            </div>
          </div>
        </div>
      </motion.section>

      <motion.section
        variants={itemVariants}
        className="mt-6 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm"
      >
        <div className="flex items-start gap-4">
          <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-accent-50 text-accent-600">
            <Brain size={22} weight="fill" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-slate-900">Memory</h2>
            <p className="mt-1 text-sm text-slate-500">
              Memory stores are not available yet. The API rejects harness memory
              references until that model ships.
            </p>
          </div>
        </div>
      </motion.section>

      <motion.section
        variants={itemVariants}
        className="mt-6 rounded-2xl border border-red-200 bg-red-50/50 p-6"
      >
        <div className="flex items-center justify-between gap-4">
          <div>
            <p className="text-sm font-semibold text-red-700">Delete harness</p>
            <p className="mt-0.5 text-xs text-red-600">This action cannot be undone</p>
          </div>
          <button
            type="button"
            onClick={() => void handleDelete()}
            disabled={deleteHarness.isPending}
            className="inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium text-red-600 hover:bg-red-100 disabled:opacity-60"
          >
            <Trash size={16} />
            {deleteHarness.isPending ? 'Deleting…' : 'Delete'}
          </button>
        </div>
      </motion.section>
    </motion.div>
  );
}

export default function EditHarnessPage(): JSX.Element {
  return (
    <AuthGuard>
      <div className="grid min-h-[100dvh] grid-cols-[auto_1fr] grid-rows-[auto_1fr] bg-gradient-subtle">
        <div className="col-span-2">
          <Header />
        </div>
        <Sidebar />
        <main className="overflow-auto">
          <HarnessDetailContent />
        </main>
      </div>
    </AuthGuard>
  );
}
