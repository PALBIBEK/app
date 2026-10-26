/**
 * Harness Sessions List - Premium Edition
 *
 * Design: Clean list with Framer Motion animations
 * Quality: Senior frontend developer level
 */

'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { motion } from 'framer-motion';
import {
  ArrowLeft,
  Play,
  MagnifyingGlass,
  FunnelSimple,
  CalendarBlank,
  Timer,
  Coins,
  CheckCircle,
  XCircle,
  Clock,
  CaretRight,
  ChatCircle,
  ArrowsClockwise,
  TrendUp,
  Cube,
} from '@phosphor-icons/react';
import { AuthGuard } from '@/components/auth/AuthGuard';
import { cn } from '@/lib/utils';
import { AppShell } from '@/components/layout/AppShell';

interface Session {
  id: string;
  status: 'completed' | 'failed' | 'running';
  startedAt: Date;
  endedAt?: Date;
  turns: number;
  tokensUsed: number;
  cost: number;
  preview: string;
}

const mockSessions: Session[] = [
  {
    id: 'sess_1',
    status: 'completed',
    startedAt: new Date(Date.now() - 2 * 60 * 60 * 1000),
    endedAt: new Date(Date.now() - 1 * 60 * 60 * 1000),
    turns: 12,
    tokensUsed: 45000,
    cost: 0.45,
    preview: 'Help me write a Python function for Fibonacci sequence...',
  },
  {
    id: 'sess_2',
    status: 'completed',
    startedAt: new Date(Date.now() - 5 * 60 * 60 * 1000),
    endedAt: new Date(Date.now() - 4 * 60 * 60 * 1000),
    turns: 8,
    tokensUsed: 32000,
    cost: 0.32,
    preview: 'Debug this React component that is not rendering...',
  },
  {
    id: 'sess_3',
    status: 'failed',
    startedAt: new Date(Date.now() - 24 * 60 * 60 * 1000),
    endedAt: new Date(Date.now() - 23 * 60 * 60 * 1000),
    turns: 3,
    tokensUsed: 8500,
    cost: 0.09,
    preview: 'Write a SQL query to join these three tables...',
  },
  {
    id: 'sess_4',
    status: 'running',
    startedAt: new Date(Date.now() - 5 * 60 * 1000),
    turns: 2,
    tokensUsed: 12000,
    cost: 0.12,
    preview: 'Implement a REST API endpoint for user authentication...',
  },
  {
    id: 'sess_5',
    status: 'completed',
    startedAt: new Date(Date.now() - 48 * 60 * 60 * 1000),
    endedAt: new Date(Date.now() - 47 * 60 * 60 * 1000),
    turns: 15,
    tokensUsed: 68000,
    cost: 0.68,
    preview: 'Refactor this legacy codebase to use modern patterns...',
  },
];

// Animation variants
const pageVariants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { staggerChildren: 0.08 } }
};

const itemVariants = {
  hidden: { opacity: 0, y: 16 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.4, ease: [0.25, 0.46, 0.45, 0.94] } }
};

const listVariants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { staggerChildren: 0.06, delayChildren: 0.1 } }
};

const cardVariants = {
  hidden: { opacity: 0, y: 20, scale: 0.98 },
  visible: { opacity: 1, y: 0, scale: 1, transition: { duration: 0.3, ease: 'easeOut' } }
};

export default function HarnessSessionsPage(): JSX.Element {
  const params = useParams();
  const [searchQuery, setSearchQuery] = useState('');

  const formatDuration = (start: Date, end?: Date): string => {
    const endTime = end || new Date();
    const diff = endTime.getTime() - start.getTime();
    const minutes = Math.floor(diff / 60000);
    if (minutes < 60) return `${minutes}m`;
    const hours = Math.floor(minutes / 60);
    return `${hours}h ${minutes % 60}m`;
  };

  const formatRelativeTime = (date: Date): string => {
    const now = new Date();
    const diff = now.getTime() - date.getTime();
    const minutes = Math.floor(diff / 60000);
    const hours = Math.floor(minutes / 60);
    const days = Math.floor(hours / 24);

    if (minutes < 60) return `${minutes}m ago`;
    if (hours < 24) return `${hours}h ago`;
    return `${days}d ago`;
  };

  const stats = [
    { label: 'Total Sessions', value: mockSessions.length.toString(), icon: Cube },
    { label: 'Success Rate', value: '80%', icon: TrendUp, accent: 'green' as const },
    { label: 'Avg Duration', value: '45m', icon: Timer },
    { label: 'Total Spend', value: '$1.66', icon: Coins },
  ];

  return (
    <AuthGuard>
    <AppShell>
        <motion.div
          className="mx-auto max-w-5xl px-8 py-8"
          initial="hidden"
          animate="visible"
          variants={pageVariants}
        >
          {/* Back link */}
          <motion.div variants={itemVariants}>
            <Link
              href={`/harness/${params.id}`}
              className="group inline-flex items-center gap-2 text-sm font-medium text-slate-500 transition-colors hover:text-slate-700"
            >
              <ArrowLeft size={16} className="transition-transform group-hover:-translate-x-1" />
              Back to Harness
            </Link>
          </motion.div>

          {/* Page header */}
          <motion.div variants={itemVariants} className="mt-6 flex items-start justify-between">
            <div className="flex items-start gap-4">
              <motion.div
                whileHover={{ scale: 1.05, rotate: -5 }}
                className="flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-primary-400 to-primary-600 shadow-lg shadow-primary-500/25"
              >
                <Clock size={26} weight="fill" className="text-white" />
              </motion.div>
              <div>
                <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
                  Sessions
                </h1>
                <p className="mt-1 text-sm text-slate-500">
                  coding-v1 / {mockSessions.length} total sessions
                </p>
              </div>
            </div>

            <motion.div whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.97 }}>
              <Link
                href={`/harness/${params.id}/test`}
                className={cn(
                  'flex items-center gap-2 rounded-xl px-5 py-2.5',
                  'text-sm font-medium text-white',
                  'bg-gradient-to-br from-primary-500 to-primary-600',
                  'shadow-lg shadow-primary-500/25',
                  'transition-shadow hover:shadow-xl hover:shadow-primary-500/30'
                )}
              >
                <Play size={16} weight="fill" />
                New Session
              </Link>
            </motion.div>
          </motion.div>

          {/* Filters */}
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

            <motion.button
              whileHover={{ scale: 1.02 }}
              whileTap={{ scale: 0.98 }}
              className={cn(
                'flex items-center gap-2 rounded-xl px-4 py-2.5',
                'text-sm font-medium text-slate-600',
                'bg-white border-2 border-slate-200',
                'shadow-sm transition-all hover:border-slate-300 hover:shadow'
              )}
            >
              <FunnelSimple size={16} />
              Filter
            </motion.button>

            <motion.button
              whileHover={{ scale: 1.02 }}
              whileTap={{ scale: 0.98 }}
              className={cn(
                'flex items-center gap-2 rounded-xl px-4 py-2.5',
                'text-sm font-medium text-slate-600',
                'bg-white border-2 border-slate-200',
                'shadow-sm transition-all hover:border-slate-300 hover:shadow'
              )}
            >
              <CalendarBlank size={16} />
              Date Range
            </motion.button>
          </motion.div>

          {/* Stats summary */}
          <motion.div variants={itemVariants} className="mt-6 grid grid-cols-4 gap-4">
            {stats.map((stat, index) => {
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
                        className={stat.accent === 'green' ? 'text-secondary-600' : 'text-slate-500'}
                      />
                    </div>
                    <div>
                      <p
                        className={cn(
                          'text-xl font-semibold',
                          stat.accent === 'green' ? 'text-secondary-600' : 'text-slate-900'
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

          {/* Sessions list */}
          <motion.div
            variants={listVariants}
            initial="hidden"
            animate="visible"
            className="mt-6 space-y-3"
          >
            {mockSessions.map((session, index) => (
              <motion.div key={session.id} variants={cardVariants}>
                <Link
                  href={`/sessions/${session.id}`}
                  className="block"
                >
                  <motion.div
                    whileHover={{ y: -4, scale: 1.005 }}
                    className={cn(
                      'group flex items-center gap-5 rounded-2xl p-5',
                      'border-2 border-slate-200/80 bg-white',
                      'shadow-sm transition-all duration-200',
                      'hover:border-slate-300 hover:shadow-lg'
                    )}
                  >
                    {/* Status indicator */}
                    <motion.div
                      whileHover={{ scale: 1.1, rotate: 5 }}
                      className={cn(
                        'flex h-12 w-12 flex-shrink-0 items-center justify-center rounded-xl shadow-md',
                        session.status === 'completed' && 'bg-gradient-to-br from-secondary-400 to-secondary-600 shadow-secondary-500/25',
                        session.status === 'failed' && 'bg-gradient-to-br from-red-400 to-red-600 shadow-red-500/25',
                        session.status === 'running' && 'bg-gradient-to-br from-primary-400 to-primary-600 shadow-primary-500/25'
                      )}
                    >
                      {session.status === 'completed' && (
                        <CheckCircle size={22} weight="fill" className="text-white" />
                      )}
                      {session.status === 'failed' && (
                        <XCircle size={22} weight="fill" className="text-white" />
                      )}
                      {session.status === 'running' && (
                        <motion.div
                          animate={{ rotate: 360 }}
                          transition={{ repeat: Infinity, duration: 1.5, ease: 'linear' }}
                        >
                          <ArrowsClockwise size={22} className="text-white" />
                        </motion.div>
                      )}
                    </motion.div>

                    {/* Main content */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-3">
                        <p className="text-sm font-semibold text-slate-900 truncate">
                          {session.preview}
                        </p>
                        <motion.span
                          initial={{ scale: 0.8 }}
                          animate={{ scale: 1 }}
                          className={cn(
                            'flex-shrink-0 rounded-full px-2.5 py-1 text-xs font-semibold',
                            session.status === 'completed' && 'bg-secondary-50 text-secondary-700 ring-1 ring-inset ring-secondary-200',
                            session.status === 'failed' && 'bg-red-50 text-red-700 ring-1 ring-inset ring-red-200',
                            session.status === 'running' && 'bg-primary-50 text-primary-700 ring-1 ring-inset ring-primary-200'
                          )}
                        >
                          {session.status}
                        </motion.span>
                      </div>

                      <div className="mt-2 flex items-center gap-5 text-xs text-slate-500">
                        <span className="flex items-center gap-1.5">
                          <Clock size={12} className="text-slate-400" />
                          {formatRelativeTime(session.startedAt)}
                        </span>
                        <span className="flex items-center gap-1.5">
                          <Timer size={12} className="text-slate-400" />
                          {formatDuration(session.startedAt, session.endedAt)}
                        </span>
                        <span className="flex items-center gap-1.5">
                          <ChatCircle size={12} className="text-slate-400" />
                          {session.turns} turns
                        </span>
                        <span className="flex items-center gap-1.5">
                          <Coins size={12} className="text-slate-400" />
                          ${session.cost.toFixed(2)}
                        </span>
                      </div>
                    </div>

                    {/* Arrow */}
                    <motion.div
                      initial={{ x: 0, opacity: 0.5 }}
                      whileHover={{ x: 4, opacity: 1 }}
                      className="transition-all"
                    >
                      <CaretRight
                        size={20}
                        weight="bold"
                        className="text-slate-300 group-hover:text-slate-500"
                      />
                    </motion.div>
                  </motion.div>
                </Link>
              </motion.div>
            ))}
          </motion.div>

          {/* Load more */}
          <motion.div variants={itemVariants} className="mt-8 flex items-center justify-center">
            <motion.button
              whileHover={{ scale: 1.02 }}
              whileTap={{ scale: 0.98 }}
              className="text-sm font-medium text-primary-600 hover:text-primary-700 transition-colors"
            >
              Load More Sessions
            </motion.button>
          </motion.div>
        </motion.div>
      </AppShell>
    </AuthGuard>
  );
}
