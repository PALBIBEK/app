/**
 * Sessions List Page — live BFF sessions (D4).
 */

'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { motion } from 'framer-motion';
import {
  ClockCounterClockwise,
  Play,
  CheckCircle,
  XCircle,
  MagnifyingGlass,
  SortAscending,
  Stack,
  Clock,
  CurrencyDollar,
  ChatCircle,
  Pause,
  Warning,
} from '@phosphor-icons/react';
import { AuthGuard } from '@/components/auth/AuthGuard';
import { Header } from '@/components/layout/Header';
import { Sidebar } from '@/components/layout/Sidebar';
import { useSessions } from '@/lib/api/hooks/useSessions';
import type { Session } from '@/lib/api/types/session.types';
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

const cardVariants = {
  hidden: { opacity: 0, scale: 0.95 },
  visible: { opacity: 1, scale: 1, transition: { duration: 0.3, ease: 'easeOut' } },
};

function statusTone(status: string): 'primary' | 'secondary' | 'red' | 'amber' | 'slate' {
  switch (status) {
    case 'active':
      return 'primary';
    case 'terminated':
      return 'secondary';
    case 'failed':
    case 'budget_exceeded':
      return 'red';
    case 'paused':
    case 'awaiting_approval':
      return 'amber';
    default:
      return 'slate';
  }
}

function StatusIconFor({ status }: { status: string }) {
  switch (status) {
    case 'active':
      return Play;
    case 'terminated':
      return CheckCircle;
    case 'failed':
    case 'budget_exceeded':
      return XCircle;
    case 'paused':
      return Pause;
    case 'awaiting_approval':
      return Warning;
    default:
      return ClockCounterClockwise;
  }
}

function formatTimestamp(iso: string): string {
  const date = new Date(iso);
  const now = new Date();
  const diff = now.getTime() - date.getTime();
  const hours = Math.floor(diff / (60 * 60 * 1000));
  const days = Math.floor(hours / 24);

  if (hours < 1) return 'Just now';
  if (hours < 24) return `${hours}h ago`;
  if (days === 1) return 'Yesterday';
  return `${days}d ago`;
}

export default function SessionsPage(): JSX.Element {
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const { data, isLoading, error } = useSessions({ page: 1, page_size: 50 });

  const sessions = data?.data ?? [];

  const filtered = useMemo(() => {
    return sessions.filter((s) => {
      const matchesStatus = statusFilter === 'all' || s.status === statusFilter;
      const q = searchQuery.trim().toLowerCase();
      const matchesSearch =
        !q ||
        s.id.toLowerCase().includes(q) ||
        s.harness_id.toLowerCase().includes(q) ||
        (s.title ?? '').toLowerCase().includes(q);
      return matchesStatus && matchesSearch;
    });
  }, [sessions, searchQuery, statusFilter]);

  const stats = {
    total: data?.total ?? sessions.length,
    active: sessions.filter((s) => s.status === 'active').length,
    terminated: sessions.filter((s) => s.status === 'terminated').length,
    totalCost: sessions.reduce((sum, s) => sum + (s.total_cost ?? 0), 0),
  };

  const filters = ['all', 'active', 'terminated', 'failed', 'paused'];

  return (
    <AuthGuard>
      <div className="grid min-h-[100dvh] grid-cols-[auto_1fr] grid-rows-[auto_1fr] bg-gradient-subtle">
        <div className="col-span-2">
          <Header />
        </div>
        <Sidebar />

        <main className="overflow-auto">
          <motion.div
            className="mx-auto max-w-6xl px-8 py-8"
            initial="hidden"
            animate="visible"
            variants={pageVariants}
          >
            <motion.div variants={itemVariants} className="flex items-start justify-between">
              <div className="flex items-start gap-4">
                <motion.div
                  whileHover={{ scale: 1.05, rotate: -5 }}
                  className="flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-primary-400 to-primary-600 shadow-lg shadow-primary-500/25"
                >
                  <ClockCounterClockwise size={26} weight="fill" className="text-white" />
                </motion.div>
                <div>
                  <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Sessions</h1>
                  <p className="mt-1 text-sm text-slate-500">
                    View and manage all conversation sessions
                  </p>
                </div>
              </div>
            </motion.div>

            <motion.div variants={itemVariants} className="mt-8 grid grid-cols-4 gap-4">
              {[
                { label: 'Total Sessions', value: stats.total, icon: Stack, color: 'primary' },
                { label: 'Active', value: stats.active, icon: Play, color: 'accent' },
                {
                  label: 'Terminated',
                  value: stats.terminated,
                  icon: CheckCircle,
                  color: 'secondary',
                },
                {
                  label: 'Total Cost',
                  value: `$${stats.totalCost.toFixed(2)}`,
                  icon: CurrencyDollar,
                  color: 'slate',
                },
              ].map((stat) => {
                const Icon = stat.icon;
                return (
                  <motion.div
                    key={stat.label}
                    whileHover={{ y: -4, scale: 1.02 }}
                    className={cn(
                      'rounded-2xl p-5',
                      'border-2 border-slate-200/80 bg-white',
                      'shadow-sm transition-all duration-200',
                      'hover:border-slate-300 hover:shadow-lg'
                    )}
                  >
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="text-sm font-medium text-slate-500">{stat.label}</p>
                        <p className="mt-1 text-2xl font-semibold text-slate-900">{stat.value}</p>
                      </div>
                      <div
                        className={cn(
                          'flex h-12 w-12 items-center justify-center rounded-xl',
                          stat.color === 'primary' && 'bg-primary-100',
                          stat.color === 'accent' && 'bg-accent-100',
                          stat.color === 'secondary' && 'bg-secondary-100',
                          stat.color === 'slate' && 'bg-slate-100'
                        )}
                      >
                        <Icon
                          size={24}
                          weight="regular"
                          className={cn(
                            stat.color === 'primary' && 'text-primary-600',
                            stat.color === 'accent' && 'text-accent-600',
                            stat.color === 'secondary' && 'text-secondary-600',
                            stat.color === 'slate' && 'text-slate-600'
                          )}
                        />
                      </div>
                    </div>
                  </motion.div>
                );
              })}
            </motion.div>

            <motion.div variants={itemVariants} className="mt-8 flex items-center gap-4">
              <div className="relative flex-1 max-w-md">
                <MagnifyingGlass
                  size={16}
                  className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-400"
                />
                <motion.input
                  whileFocus={{ scale: 1.01 }}
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="Search sessions..."
                  className={cn(
                    'h-11 w-full rounded-xl pl-11 pr-4',
                    'text-sm text-slate-900 placeholder:text-slate-400',
                    'bg-white border-2 border-slate-200',
                    'shadow-sm transition-all duration-200',
                    'hover:border-slate-300',
                    'focus:border-primary-500 focus:outline-none focus:ring-4 focus:ring-primary-500/10'
                  )}
                />
              </div>

              <div className="flex flex-wrap items-center gap-2">
                {filters.map((filter) => (
                  <motion.button
                    key={filter}
                    whileHover={{ scale: 1.02 }}
                    whileTap={{ scale: 0.98 }}
                    onClick={() => setStatusFilter(filter)}
                    className={cn(
                      'rounded-xl px-4 py-2',
                      'text-sm font-medium transition-all',
                      statusFilter === filter
                        ? 'bg-primary-600 text-white shadow-md shadow-primary-500/25'
                        : 'bg-white text-slate-600 border-2 border-slate-200 hover:border-slate-300'
                    )}
                  >
                    <span className="capitalize">{filter.replace('_', ' ')}</span>
                  </motion.button>
                ))}
              </div>

              <motion.button
                whileHover={{ scale: 1.02 }}
                whileTap={{ scale: 0.98 }}
                type="button"
                className={cn(
                  'flex items-center gap-2 rounded-xl px-4 py-2.5',
                  'text-sm font-medium text-slate-600',
                  'bg-white border-2 border-slate-200',
                  'shadow-sm transition-all hover:border-slate-300 hover:shadow'
                )}
              >
                <SortAscending size={16} />
                Sort
              </motion.button>
            </motion.div>

            {isLoading && (
              <p className="mt-8 text-sm text-slate-500">Loading sessions…</p>
            )}
            {error && (
              <p className="mt-8 text-sm text-red-600">
                {error instanceof Error ? error.message : 'Failed to load sessions'}
              </p>
            )}

            {!isLoading && !error && filtered.length === 0 && (
              <p className="mt-8 text-sm text-slate-500">No sessions found.</p>
            )}

            <motion.div variants={itemVariants} className="mt-6 grid grid-cols-2 gap-4">
              {filtered.map((session) => (
                <SessionCard key={session.id} session={session} />
              ))}
            </motion.div>
          </motion.div>
        </main>
      </div>
    </AuthGuard>
  );
}

function SessionCard({ session }: { session: Session }): JSX.Element {
  const StatusIcon = StatusIconFor({ status: session.status });
  const tone = statusTone(session.status);
  const isActive = session.status === 'active';

  return (
    <Link href={`/sessions/${session.id}`}>
      <motion.div
        variants={cardVariants}
        whileHover={{ y: -4, scale: 1.01 }}
        className={cn(
          'rounded-2xl p-5',
          'border-2 border-slate-200/80 bg-white',
          'shadow-sm transition-all duration-200',
          'hover:border-slate-300 hover:shadow-lg'
        )}
      >
        <div className="flex items-start justify-between">
          <div className="flex items-start gap-3">
            <div
              className={cn(
                'flex h-11 w-11 items-center justify-center rounded-xl',
                tone === 'secondary' && 'bg-secondary-100',
                tone === 'red' && 'bg-red-100',
                tone === 'primary' && 'bg-primary-100',
                tone === 'amber' && 'bg-amber-100',
                tone === 'slate' && 'bg-slate-100'
              )}
            >
              {isActive ? (
                <motion.div
                  animate={{ rotate: 360 }}
                  transition={{ duration: 2, repeat: Infinity, ease: 'linear' }}
                >
                  <StatusIcon
                    size={20}
                    weight="fill"
                    className={cn(
                      tone === 'secondary' && 'text-secondary-600',
                      tone === 'red' && 'text-red-600',
                      tone === 'primary' && 'text-primary-600',
                      tone === 'amber' && 'text-amber-600',
                      tone === 'slate' && 'text-slate-600'
                    )}
                  />
                </motion.div>
              ) : (
                <StatusIcon
                  size={20}
                  weight="fill"
                  className={cn(
                    tone === 'secondary' && 'text-secondary-600',
                    tone === 'red' && 'text-red-600',
                    tone === 'primary' && 'text-primary-600',
                    tone === 'amber' && 'text-amber-600',
                    tone === 'slate' && 'text-slate-600'
                  )}
                />
              )}
            </div>
            <div>
              <p className="text-sm font-semibold text-slate-900">
                {session.title || session.harness_id}
              </p>
              <p className="mt-0.5 text-xs font-mono text-slate-500">{session.id}</p>
            </div>
          </div>

          <span
            className={cn(
              'flex items-center gap-1.5 rounded-full px-2.5 py-1',
              tone === 'secondary' &&
                'bg-secondary-50 text-secondary-700 ring-1 ring-inset ring-secondary-200',
              tone === 'red' && 'bg-red-50 text-red-700 ring-1 ring-inset ring-red-200',
              tone === 'primary' &&
                'bg-primary-50 text-primary-700 ring-1 ring-inset ring-primary-200',
              tone === 'amber' && 'bg-amber-50 text-amber-700 ring-1 ring-inset ring-amber-200',
              tone === 'slate' && 'bg-slate-50 text-slate-700 ring-1 ring-inset ring-slate-200'
            )}
          >
            <span className="text-xs font-semibold capitalize">
              {session.status.replace('_', ' ')}
            </span>
          </span>
        </div>

        <div className="mt-4 grid grid-cols-3 gap-3">
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-slate-100">
              <Clock size={14} weight="regular" className="text-slate-600" />
            </div>
            <div>
              <p className="text-xs text-slate-500">Updated</p>
              <p className="text-xs font-semibold text-slate-900">
                {formatTimestamp(session.updated_at)}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-slate-100">
              <ChatCircle size={14} weight="regular" className="text-slate-600" />
            </div>
            <div>
              <p className="text-xs text-slate-500">Messages</p>
              <p className="text-xs font-semibold text-slate-900">{session.message_count}</p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-slate-100">
              <CurrencyDollar size={14} weight="regular" className="text-slate-600" />
            </div>
            <div>
              <p className="text-xs text-slate-500">Cost</p>
              <p className="text-xs font-semibold text-slate-900">
                ${session.total_cost.toFixed(3)}
              </p>
            </div>
          </div>
        </div>

        <div className="mt-4 pt-4 border-t-2 border-slate-100">
          <p className="text-xs text-slate-500">
            Started {formatTimestamp(session.created_at)} · seq {session.last_seq}
          </p>
        </div>
      </motion.div>
    </Link>
  );
}
