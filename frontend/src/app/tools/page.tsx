/**
 * Tools Library — live BFF CRUD via useTools (D6 §5 F / LOCAL_DEMO #3).
 */

'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { motion } from 'framer-motion';
import {
  MagnifyingGlass,
  Plus,
  Wrench,
  Code,
  Cube,
  Lightning,
  CheckCircle,
  ArrowRight,
  Stack,
  SpinnerGap,
  WarningCircle,
} from '@phosphor-icons/react';
import { AuthGuard } from '@/components/auth/AuthGuard';
import { useTools } from '@/lib/api/hooks/useTools';
import type { Tool, ToolCategory } from '@/lib/api/types/tool.types';
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

const listVariants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { staggerChildren: 0.05, delayChildren: 0.1 } },
};

const cardVariants = {
  hidden: { opacity: 0, y: 20, scale: 0.98 },
  visible: {
    opacity: 1,
    y: 0,
    scale: 1,
    transition: { duration: 0.3, ease: 'easeOut' },
  },
};

type FilterId = 'all' | ToolCategory;

function sourceBadge(tool: Tool): { label: string; tone: 'primary' | 'secondary' | 'accent' } {
  if (tool.category === 'custom') {
    return { label: 'custom', tone: 'secondary' };
  }
  if (tool.endpoint) {
    return { label: 'http', tone: 'accent' };
  }
  return { label: 'shipped', tone: 'primary' };
}

function ToolsLibraryContent(): JSX.Element {
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedCategory, setSelectedCategory] = useState<FilterId>('all');
  const { data, isLoading, error } = useTools({ page: 1, page_size: 100 });

  const tools = useMemo(() => data?.data ?? [], [data]);

  const filteredTools = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    return tools.filter((tool) => {
      const matchesSearch =
        !q ||
        tool.name.toLowerCase().includes(q) ||
        tool.description.toLowerCase().includes(q);
      const matchesCategory =
        selectedCategory === 'all' || tool.category === selectedCategory;
      return matchesSearch && matchesCategory;
    });
  }, [tools, searchQuery, selectedCategory]);

  const activeCount = tools.filter((t) => t.status === 'active').length;

  const categoryFilters: { id: FilterId; label: string; count: number }[] = [
    { id: 'all', label: 'All Tools', count: tools.length },
    {
      id: 'custom',
      label: 'Custom',
      count: tools.filter((t) => t.category === 'custom').length,
    },
    {
      id: 'search',
      label: 'Search',
      count: tools.filter((t) => t.category === 'search').length,
    },
    {
      id: 'code',
      label: 'Code',
      count: tools.filter((t) => t.category === 'code').length,
    },
    {
      id: 'data',
      label: 'Data',
      count: tools.filter((t) => t.category === 'data').length,
    },
    {
      id: 'system',
      label: 'System',
      count: tools.filter((t) => t.category === 'system').length,
    },
    {
      id: 'ai',
      label: 'AI',
      count: tools.filter((t) => t.category === 'ai').length,
    },
    {
      id: 'communication',
      label: 'Comm',
      count: tools.filter((t) => t.category === 'communication').length,
    },
  ];

  const stats = [
    { label: 'Total Tools', value: String(tools.length), icon: Stack },
    {
      label: 'Active',
      value: String(activeCount),
      icon: CheckCircle,
      accent: 'green' as const,
    },
  ];

  return (
    <motion.div
      className="mx-auto max-w-6xl px-8 py-8"
      initial="hidden"
      animate="visible"
      variants={pageVariants}
    >
      <motion.div variants={itemVariants} className="flex items-start justify-between">
        <div className="flex items-start gap-4">
          <div className="flex h-14 w-14 items-center justify-center rounded-card bg-accent-500">
            <Wrench size={26} weight="fill" className="text-white" />
          </div>
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-ink">
              Tools Library
            </h1>
            <p className="mt-1 text-sm text-ink-2">
              Create and manage custom tools attached to harnesses
            </p>
          </div>
        </div>

        <Link
          href="/tools/new"
          className={cn(
            'flex items-center gap-2 rounded-xl px-5 py-2.5',
            'text-sm font-medium text-white',
            'bg-accent-500',
            ''
          )}
        >
          <Plus size={16} weight="bold" />
          New Tool
        </Link>
      </motion.div>

      <motion.div variants={itemVariants} className="mt-8 grid grid-cols-2 gap-4 sm:grid-cols-4">
        {stats.map((stat) => {
          const Icon = stat.icon;
          return (
            <div
              key={stat.label}
              className="rounded-card border border-line bg-surface p-5"
            >
              <div className="flex items-center gap-3">
                <div
                  className={cn(
                    'flex h-10 w-10 items-center justify-center rounded-xl',
                    stat.accent === 'green' ? 'bg-line-soft' : 'bg-line-soft'
                  )}
                >
                  <Icon
                    size={18}
                    weight="fill"
                    className={
                      stat.accent === 'green' ? 'text-good' : 'text-ink-2'
                    }
                  />
                </div>
                <div>
                  <p
                    className={cn(
                      'text-xl font-semibold',
                      stat.accent === 'green' ? 'text-good' : 'text-ink'
                    )}
                  >
                    {stat.value}
                  </p>
                  <p className="text-xs text-ink-2">{stat.label}</p>
                </div>
              </div>
            </div>
          );
        })}
      </motion.div>

      <motion.div variants={itemVariants} className="mt-8 flex items-center gap-4">
        <div className="relative max-w-md flex-1">
          <MagnifyingGlass
            size={16}
            className="absolute left-4 top-1/2 -translate-y-1/2 text-ink-3"
          />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search tools..."
            className={cn(
              'h-11 w-full rounded-xl pl-11 pr-4',
              'text-sm text-ink placeholder:text-ink-3',
              'border border-line bg-surface',
              'focus:border-accent-500 focus:outline-none focus:ring-4 focus:ring-accent-500/10'
            )}
          />
        </div>
      </motion.div>

      <motion.div variants={itemVariants} className="mt-6 flex flex-wrap items-center gap-2">
        {categoryFilters.map((filter) => (
          <button
            key={filter.id}
            type="button"
            onClick={() => setSelectedCategory(filter.id)}
            className={cn(
              'rounded-xl px-4 py-2 text-sm font-medium transition-all',
              selectedCategory === filter.id
                ? 'bg-accent-600 text-white'
                : 'border border-line bg-surface text-ink-2 hover:border-line-strong'
            )}
          >
            {filter.label}
            <span
              className={cn(
                'ml-2 rounded-full px-2 py-0.5 text-xs font-semibold',
                selectedCategory === filter.id
                  ? 'bg-surface text-white'
                  : 'bg-line-soft text-ink-2'
              )}
            >
              {filter.count}
            </span>
          </button>
        ))}
      </motion.div>

      {isLoading ? (
        <div className="mt-16 flex items-center justify-center gap-3 text-ink-2">
          <SpinnerGap size={24} className="animate-spin" />
          Loading tools…
        </div>
      ) : error ? (
        <div className="mt-12 rounded-card border border-line bg-canvas p-8 text-center">
          <WarningCircle size={32} className="mx-auto text-red-500" />
          <p className="mt-3 text-sm font-semibold text-bad">
            Could not load tools. Check that you are signed in.
          </p>
        </div>
      ) : (
        <motion.div
          variants={listVariants}
          initial="hidden"
          animate="visible"
          className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-2"
        >
          {filteredTools.map((tool) => {
            const badge = sourceBadge(tool);
            const Icon =
              tool.category === 'custom' ? Code : tool.endpoint ? Lightning : Wrench;
            return (
              <motion.div key={tool.id} variants={cardVariants}>
                <Link href={`/tools/${tool.id}`} className="block">
                  <div
                    className={cn(
                      'group flex items-start gap-4 rounded-card p-5',
                      'border border-line bg-surface',
                      'transition-all hover:border-line-strong'
                    )}
                  >
                    <div
                      className={cn(
                        'flex h-12 w-12 flex-shrink-0 items-center justify-center rounded-xl',
                        badge.tone === 'primary' &&
                          'bg-accent-500',
                        badge.tone === 'secondary' &&
                          'bg-ink',
                        badge.tone === 'accent' &&
                          'bg-line-soft'
                      )}
                    >
                      <Icon size={22} weight="fill" className="text-white" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <h3 className="text-sm font-semibold text-ink">{tool.name}</h3>
                        <span
                          className={cn(
                            'flex-shrink-0 rounded-full px-2 py-0.5 text-xs font-semibold capitalize',
                            badge.tone === 'primary' &&
                              'bg-primary-50 text-accent-700 ring-1 ring-inset ring-accent-200',
                            badge.tone === 'secondary' &&
                              'bg-line-soft text-good ring-1 ring-inset ring-secondary-200',
                            badge.tone === 'accent' &&
                              'bg-accent-50 text-accent-700 ring-1 ring-inset ring-accent-200'
                          )}
                        >
                          {badge.label}
                        </span>
                        <span className="rounded-full bg-line-soft px-2 py-0.5 text-xs text-ink-2">
                          {tool.category}
                        </span>
                      </div>
                      <p className="mt-1 line-clamp-2 text-xs text-ink-2">
                        {tool.description}
                      </p>
                      <div className="mt-3 flex items-center gap-4 text-xs text-ink-3">
                        <span className="flex items-center gap-1">
                          <Cube size={12} />
                          {tool.status}
                        </span>
                      </div>
                    </div>
                    <ArrowRight
                      size={18}
                      weight="bold"
                      className="self-center text-ink-3 group-hover:text-ink-2"
                    />
                  </div>
                </Link>
              </motion.div>
            );
          })}
        </motion.div>
      )}

      {!isLoading && !error && filteredTools.length === 0 && (
        <div className="mt-12 flex flex-col items-center justify-center text-center">
          <div className="flex h-16 w-16 items-center justify-center rounded-card bg-line-soft">
            <Wrench size={28} className="text-ink-3" />
          </div>
          <p className="mt-4 text-sm font-medium text-ink-2">No tools found</p>
          <p className="mt-1 text-sm text-ink-2">
            Create a custom tool to attach it to a harness
          </p>
          <Link
            href="/tools/new"
            className="mt-4 text-sm font-medium text-accent-600 hover:text-accent-700"
          >
            New Tool
          </Link>
        </div>
      )}
    </motion.div>
  );
}

export default function ToolsPage(): JSX.Element {
  return (
    <AuthGuard>
      <AppShell>
          <ToolsLibraryContent />
        </AppShell>
    </AuthGuard>
  );
}
