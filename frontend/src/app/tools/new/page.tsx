/**
 * Create Tool — live useCreateTool → BFF POST /tools (D6 LOCAL_DEMO #3).
 */

'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { motion } from 'framer-motion';
import {
  ArrowLeft,
  CaretRight,
  Info,
  Sparkle,
  Tag,
} from '@phosphor-icons/react';
import { AuthGuard } from '@/components/auth/AuthGuard';
import { useCreateTool } from '@/lib/api/hooks/useTools';
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

function NewToolContent(): JSX.Element {
  const router = useRouter();
  const createTool = useCreateTool();
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [category, setCategory] = useState<ToolCategory>('custom');
  const [endpoint, setEndpoint] = useState('');
  const [authRequired, setAuthRequired] = useState(false);

  async function handleCreate(): Promise<void> {
    const trimmed = name.trim();
    if (!trimmed || !description.trim()) {
      return;
    }
    try {
      const created = await createTool.mutateAsync({
        name: trimmed,
        description: description.trim(),
        category,
        status: 'active',
        parameters: [],
        auth_required: authRequired,
        ...(endpoint.trim() ? { endpoint: endpoint.trim() } : {}),
      });
      router.push(`/tools/${created.id}`);
    } catch {
      // toast via useCreateTool
    }
  }

  return (
    <motion.div
      className="mx-auto max-w-4xl px-8 py-8"
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

      <motion.div variants={itemVariants} className="mt-6 flex items-start gap-4">
        <div className="flex h-14 w-14 items-center justify-center rounded-card bg-accent-500">
          <Sparkle size={26} weight="fill" className="text-white" />
        </div>
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-ink">
            Create New Tool
          </h1>
          <p className="mt-1 text-sm text-ink-2">
            Describe a custom HTTP tool (harnesses cannot run custom tools yet)
          </p>
        </div>
      </motion.div>

      <div className="mt-8 space-y-8">
        <motion.div variants={itemVariants}>
          <div className="flex items-start gap-4">
            <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-line-soft">
              <Tag size={20} className="text-ink-2" />
            </div>
            <div className="flex-1">
              <h2 className="text-lg font-semibold text-ink">Basic information</h2>
              <p className="mt-0.5 text-sm text-ink-2">
                Name becomes the catalog tool name after publish
              </p>
            </div>
          </div>

          <div className="mt-4 space-y-4">
            <div>
              <label className="mb-2 block text-sm font-medium text-ink-2">
                Tool name
                <span className="ml-1 text-xs text-ink-3">(lowercase, underscores)</span>
              </label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="local_demo_custom_echo"
                className={cn(
                  'h-11 w-full rounded-xl px-4 text-sm',
                  'border border-line bg-surface',
                  'focus:border-accent-500 focus:outline-none focus:ring-4 focus:ring-accent-500/10'
                )}
              />
            </div>

            <div>
              <label className="mb-2 block text-sm font-medium text-ink-2">Category</label>
              <select
                value={category}
                onChange={(e) => setCategory(e.target.value as ToolCategory)}
                className={cn(
                  'h-11 w-full rounded-xl px-4 text-sm',
                  'border border-line bg-surface',
                  'focus:border-accent-500 focus:outline-none focus:ring-4 focus:ring-accent-500/10'
                )}
              >
                {TOOL_CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="mb-2 block text-sm font-medium text-ink-2">
                Description
              </label>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Describe what this tool does…"
                rows={3}
                className={cn(
                  'w-full rounded-xl px-4 py-3 text-sm',
                  'border border-line bg-surface',
                  'focus:border-accent-500 focus:outline-none focus:ring-4 focus:ring-accent-500/10',
                  'resize-none'
                )}
              />
            </div>

            <div>
              <label className="mb-2 block text-sm font-medium text-ink-2">
                Endpoint URL (optional HTTP custom tool)
              </label>
              <input
                type="url"
                value={endpoint}
                onChange={(e) => setEndpoint(e.target.value)}
                placeholder="https://api.example.com/tool"
                className={cn(
                  'h-11 w-full rounded-xl px-4 text-sm',
                  'border border-line bg-surface',
                  'focus:border-accent-500 focus:outline-none focus:ring-4 focus:ring-accent-500/10'
                )}
              />
            </div>

            <label className="flex items-center gap-2 text-sm text-ink-2">
              <input
                type="checkbox"
                checked={authRequired}
                onChange={(e) => setAuthRequired(e.target.checked)}
                className="rounded border-line-strong"
              />
              Auth required
            </label>
          </div>
        </motion.div>

        <motion.div
          variants={itemVariants}
          className="flex items-start gap-4 rounded-xl border border-line bg-surface p-5"
        >
          <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-accent-50">
            <Info size={20} className="text-accent-600" />
          </div>
          <div>
            <p className="text-sm font-semibold text-ink">Not runnable by a harness yet</p>
            <p className="mt-1 text-sm leading-relaxed text-ink-2">
              The supervisor runs only its own tools, which you pick on a harness&apos;s Tools tab.
              A tool saved here is kept for when custom HTTP tools are supported.
            </p>
          </div>
        </motion.div>
      </div>

      <motion.div
        variants={itemVariants}
        className="mt-8 flex items-center justify-between border-t-2 border-line pt-6"
      >
        <Link
          href="/tools"
          className="text-sm font-medium text-ink-2 hover:text-ink-2"
        >
          Cancel
        </Link>
        <button
          type="button"
          disabled={createTool.isPending || !name.trim() || !description.trim()}
          onClick={() => void handleCreate()}
          className={cn(
            'flex items-center gap-2 rounded-xl px-5 py-2.5',
            'text-sm font-medium text-white',
            'bg-accent-500',
            'disabled:opacity-60'
          )}
        >
          {createTool.isPending ? 'Creating…' : 'Create Tool'}
          <CaretRight size={16} weight="bold" />
        </button>
      </motion.div>
    </motion.div>
  );
}

export default function NewToolPage(): JSX.Element {
  return (
    <AuthGuard>
      <AppShell>
          <NewToolContent />
        </AppShell>
    </AuthGuard>
  );
}
