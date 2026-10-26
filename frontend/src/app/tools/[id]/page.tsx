/**
 * Tool detail — live useTool / useUpdateTool / useDeleteTool (D6).
 */

'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { motion } from 'framer-motion';
import {
  ArrowLeft,
  Wrench,
  PencilSimple,
  Trash,
  CheckCircle,
  SpinnerGap,
  WarningCircle,
} from '@phosphor-icons/react';
import { AuthGuard } from '@/components/auth/AuthGuard';
import {
  useDeleteTool,
  useTool,
  useUpdateTool,
} from '@/lib/api/hooks/useTools';
import { TOOL_CATEGORIES, type ToolCategory } from '@/lib/api/types/tool.types';
import { cn } from '@/lib/utils';
import { AppShell } from '@/components/layout/AppShell';

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

function ToolDetailContent(): JSX.Element {
  const params = useParams();
  const router = useRouter();
  const id = typeof params.id === 'string' ? params.id : '';
  const { data: tool, isLoading, error } = useTool(id);
  const updateTool = useUpdateTool();
  const deleteTool = useDeleteTool();

  const [isEditing, setIsEditing] = useState(false);
  const [description, setDescription] = useState('');
  const [category, setCategory] = useState<ToolCategory>('custom');
  const [status, setStatus] = useState<'active' | 'inactive' | 'deprecated' | 'beta'>('active');
  const [endpoint, setEndpoint] = useState('');

  useEffect(() => {
    if (!tool) return;
    setDescription(tool.description);
    setCategory(tool.category);
    setStatus(tool.status);
    setEndpoint(tool.endpoint ?? '');
  }, [tool]);

  async function handleSave(): Promise<void> {
    if (!tool) return;
    await updateTool.mutateAsync({
      id: tool.id,
      data: {
        description: description.trim(),
        category,
        status,
        endpoint: endpoint.trim() || null,
      },
    });
    setIsEditing(false);
  }

  async function handleDelete(): Promise<void> {
    if (!tool) return;
    if (!window.confirm(`Delete tool “${tool.name}”? This cannot be undone.`)) {
      return;
    }
    await deleteTool.mutateAsync(tool.id);
    router.push('/tools');
  }

  if (isLoading) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center gap-3 text-ink-2">
        <SpinnerGap size={24} className="animate-spin" />
        Loading tool…
      </div>
    );
  }

  if (error || !tool) {
    return (
      <div className="mx-auto max-w-lg rounded-card border border-line bg-canvas p-8 text-center">
        <WarningCircle size={32} className="mx-auto text-red-500" />
        <p className="mt-3 text-sm font-semibold text-bad">
          Tool not found or you do not have access.
        </p>
        <Link
          href="/tools"
          className="mt-4 inline-flex text-sm font-medium text-accent-600 hover:text-accent-700"
        >
          Back to tools
        </Link>
      </div>
    );
  }

  const createdLabel = tool.created_at
    ? new Date(tool.created_at).toLocaleDateString()
    : '—';

  return (
    <motion.div
      className="mx-auto max-w-5xl px-8 py-8"
      initial="hidden"
      animate="visible"
      variants={pageVariants}
    >
      <motion.div variants={itemVariants}>
        <Link
          href="/tools"
          className="group inline-flex items-center gap-2 text-sm font-medium text-ink-2 transition-colors hover:text-ink-2"
        >
          <ArrowLeft size={16} className="transition-transform group-hover:-translate-x-1" />
          Back to Tools
        </Link>
      </motion.div>

      <motion.div variants={itemVariants} className="mt-6">
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-start gap-4">
            <div className="flex h-14 w-14 items-center justify-center rounded-card bg-ink">
              <Wrench size={26} weight="fill" className="text-white" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-2xl font-semibold tracking-tight text-ink">
                  {tool.name}
                </h1>
                <span className="rounded-full bg-line-soft px-2.5 py-1 text-xs font-semibold capitalize text-good ring-1 ring-inset ring-secondary-200">
                  {tool.category}
                </span>
              </div>
              <p className="mt-1 text-sm text-ink-2">{tool.description}</p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => {
                if (isEditing) {
                  void handleSave();
                } else {
                  setIsEditing(true);
                }
              }}
              disabled={updateTool.isPending}
              className={cn(
                'flex items-center gap-2 rounded-xl px-4 py-2.5 text-sm font-medium',
                isEditing
                  ? 'bg-accent-600 text-white'
                  : 'border border-line bg-surface text-ink-2 hover:border-line-strong'
              )}
            >
              <PencilSimple size={16} />
              {isEditing
                ? updateTool.isPending
                  ? 'Saving…'
                  : 'Save'
                : 'Edit'}
            </button>
            <button
              type="button"
              onClick={() => void handleDelete()}
              disabled={deleteTool.isPending}
              className={cn(
                'flex h-10 w-10 items-center justify-center rounded-xl',
                'border border-line bg-surface text-ink-3',
                'hover:border-line hover:bg-canvas hover:text-bad'
              )}
              aria-label="Delete tool"
            >
              <Trash size={16} />
            </button>
          </div>
        </div>
      </motion.div>

      <motion.div variants={itemVariants} className="mt-8 grid grid-cols-3 gap-4">
        <div className="rounded-card border border-line bg-surface p-5">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-line-soft">
              <CheckCircle size={18} weight="fill" className="text-good" />
            </div>
            <div>
              <p className="text-xl font-semibold capitalize text-good">
                {tool.status}
              </p>
              <p className="text-xs text-ink-2">Status</p>
            </div>
          </div>
        </div>
        <div className="rounded-card border border-line bg-surface p-5">
          <p className="text-sm font-semibold text-ink">{createdLabel}</p>
          <p className="text-xs text-ink-2">Created</p>
        </div>
        <div className="rounded-card border border-line bg-surface p-5">
          <p className="truncate font-mono text-xs text-ink-2">{tool.id}</p>
          <p className="text-xs text-ink-2">UUID</p>
        </div>
      </motion.div>

      <motion.div
        variants={itemVariants}
        className="mt-8 space-y-6 rounded-card border border-line bg-surface p-6"
      >
        <h3 className="text-sm font-semibold text-ink">Metadata</h3>
        {isEditing ? (
          <div className="space-y-4">
            <div>
              <label className="mb-1 block text-xs text-ink-2">Description</label>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={3}
                className="w-full rounded-xl border border-line px-3 py-2 text-sm focus:border-accent-500 focus:outline-none"
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="mb-1 block text-xs text-ink-2">Category</label>
                <select
                  value={category}
                  onChange={(e) => setCategory(e.target.value as ToolCategory)}
                  className="h-10 w-full rounded-xl border border-line px-3 text-sm"
                >
                  {TOOL_CATEGORIES.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="mb-1 block text-xs text-ink-2">Status</label>
                <select
                  value={status}
                  onChange={(e) =>
                    setStatus(e.target.value as typeof status)
                  }
                  className="h-10 w-full rounded-xl border border-line px-3 text-sm"
                >
                  {(['active', 'inactive', 'deprecated', 'beta'] as const).map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <div>
              <label className="mb-1 block text-xs text-ink-2">Endpoint</label>
              <input
                type="url"
                value={endpoint}
                onChange={(e) => setEndpoint(e.target.value)}
                className="h-10 w-full rounded-xl border border-line px-3 text-sm"
                placeholder="https://…"
              />
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-4">
            <div>
              <p className="mb-1 text-xs text-ink-2">Tool Name</p>
              <p className="font-mono text-sm text-ink">{tool.name}</p>
            </div>
            <div>
              <p className="mb-1 text-xs text-ink-2">Category</p>
              <p className="text-sm text-ink">{tool.category}</p>
            </div>
            <div>
              <p className="mb-1 text-xs text-ink-2">Endpoint</p>
              <p className="truncate text-sm text-ink">{tool.endpoint || '—'}</p>
            </div>
            <div>
              <p className="mb-1 text-xs text-ink-2">Auth required</p>
              <p className="text-sm text-ink">{tool.auth_required ? 'Yes' : 'No'}</p>
            </div>
          </div>
        )}
      </motion.div>
    </motion.div>
  );
}

export default function ToolDetailPage(): JSX.Element {
  return (
    <AuthGuard>
      <AppShell>
          <ToolDetailContent />
        </AppShell>
    </AuthGuard>
  );
}
