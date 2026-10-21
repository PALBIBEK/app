/**
 * Dashboard Page - Production Edition
 *
 * Design: Elegant asymmetric grid, refined typography, subtle depth
 * Icons: Phosphor
 * Pattern: Following corridor-platforms RestService pattern
 *
 * Architecture:
 * - TanStack Query for data fetching (equivalent to corridor's RxJS)
 * - Loading states with skeletons
 * - Error boundaries
 * - Real-time stats calculation
 */

'use client';

import { FunnelSimple, TrendUp, Pulse, Timer, Stack, WarningCircle } from '@phosphor-icons/react';
import Link from 'next/link';
import { Header } from '@/components/layout/Header';
import { Sidebar } from '@/components/layout/Sidebar';
import { HarnessCard } from '@/components/harness/HarnessCard';
import { AuthGuard } from '@/components/auth/AuthGuard';
import { AiPlatformBanner } from '@/components/platform/AiPlatformBanner';
import { cn } from '@/lib/utils';
import { useHarnesses } from '@/lib/api/hooks/useHarnesses';
import { useMemo } from 'react';
import { useSessions } from '@/lib/api/hooks/useSessions';
import { dashboardStats } from '@/lib/dashboard/stats';

export default function DashboardPage(): JSX.Element {
  const { data, isLoading, error } = useHarnesses();

  const { data: sessions } = useSessions({ page: 1, page_size: 100 });
  const stats = useMemo(() => dashboardStats(data?.data, sessions), [data, sessions]);

  return (
    <AuthGuard>
    <div className="grid min-h-[100dvh] grid-cols-[auto_1fr] grid-rows-[auto_1fr] bg-gradient-subtle">
      {/* Header spans full width */}
      <div className="col-span-2">
        <Header />
      </div>

      {/* Sidebar */}
      <Sidebar />

      {/* Main content */}
      <main className="overflow-auto">
        <div className="mx-auto max-w-6xl px-8 py-8">
          <AiPlatformBanner className="mb-6" />
          {/* Page header */}
          <div className="grid grid-cols-12 items-end gap-6">
            <div className="col-span-8">
              <p className="text-sm font-medium text-primary-600">Dashboard</p>
              <h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-900">
                Your Harnesses
              </h1>
              <p className="mt-2 text-[15px] text-slate-500">
                Build, test, and manage your agent configurations
              </p>
            </div>
            <div className="col-span-4 flex items-center justify-end gap-3">
              <button
                className={cn(
                  'inline-flex items-center gap-2 rounded-xl px-4 py-2.5',
                  'text-sm font-medium text-slate-700',
                  'bg-white border border-slate-200',
                  'shadow-sm',
                  'transition-all duration-150',
                  'hover:bg-slate-50 hover:border-slate-300',
                  'focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-500/20',
                  'active:scale-[0.98]'
                )}
              >
                <FunnelSimple size={16} />
                Filter
              </button>
            </div>
          </div>

          {/* Stats row - Production pattern: Loading skeletons */}
          <div className="mt-8 grid grid-cols-4 gap-4">
            {isLoading ? (
              // Loading skeletons (corridor pattern)
              <>
                <StatCardSkeleton />
                <StatCardSkeleton />
                <StatCardSkeleton />
                <StatCardSkeleton />
              </>
            ) : (
              <>
                <StatCard
                  icon={Stack}
                  label="Total Harnesses"
                  value={stats.harnesses.toString()}
                  sublabel="configurations"
                />
                <StatCard
                  icon={Pulse}
                  label="Live"
                  value={stats.live.toString()}
                  sublabel="published and ready"
                  accent="green"
                />
                <StatCard
                  icon={TrendUp}
                  label="Sessions"
                  value={stats.sessions.toString()}
                  sublabel="all time"
                />
                <StatCard
                  icon={Timer}
                  label="Today"
                  value={`${stats.today}${stats.todayCapped ? '+' : ''}`}
                  sublabel="sessions"
                  accent="blue"
                />
              </>
            )}
          </div>

          {/* Section header */}
          <div className="mt-10 flex items-center justify-between">
            <h2 className="text-lg font-semibold tracking-tight text-slate-900">
              All Harnesses
            </h2>
            <div className="flex items-center gap-2">
              <button className="rounded-lg px-3 py-1.5 text-sm font-medium text-primary-600 hover:bg-primary-50 transition-colors">
                Recent
              </button>
              <button className="rounded-lg px-3 py-1.5 text-sm font-medium text-slate-500 hover:bg-slate-100 hover:text-slate-700 transition-colors">
                Name
              </button>
              <button className="rounded-lg px-3 py-1.5 text-sm font-medium text-slate-500 hover:bg-slate-100 hover:text-slate-700 transition-colors">
                Status
              </button>
            </div>
          </div>

          {/* Harness list - Production pattern: Loading, Error, Empty, Data states */}
          <div className="mt-4 space-y-3">
            {isLoading && (
              // Loading state (corridor pattern: skeleton loaders)
              <>
                <HarnessCardSkeleton />
                <HarnessCardSkeleton />
                <HarnessCardSkeleton />
              </>
            )}

            {error && (
              // Error state (corridor pattern: user-friendly error messages)
              <div className="rounded-2xl border border-red-200 bg-red-50 p-6">
                <div className="flex items-start gap-4">
                  <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl bg-red-100">
                    <WarningCircle size={20} className="text-red-600" weight="fill" />
                  </div>
                  <div>
                    <p className="text-sm font-medium text-red-900">
                      Failed to load harnesses
                    </p>
                    <p className="mt-1 text-sm text-red-700">
                      {error instanceof Error ? error.message : 'An unexpected error occurred. Please try again.'}
                    </p>
                    <button
                      onClick={() => window.location.reload()}
                      className="mt-3 text-sm font-medium text-red-600 hover:text-red-700 transition-colors"
                    >
                      Reload page
                    </button>
                  </div>
                </div>
              </div>
            )}

            {!isLoading && !error && data?.data && data.data.length === 0 && (
              // Empty state
              <div className="rounded-2xl border border-dashed border-slate-200 bg-white/50 p-8 text-center">
                <div className="flex h-12 w-12 mx-auto items-center justify-center rounded-xl bg-slate-100">
                  <Stack size={20} className="text-slate-500" />
                </div>
                <p className="mt-4 text-sm font-medium text-slate-700">
                  No harnesses yet
                </p>
                <p className="mt-1 text-sm text-slate-500">
                  Get started by creating your first agent configuration
                </p>
                <Link
                  href="/harness/new"
                  className="mt-4 inline-flex rounded-xl bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700 transition-colors"
                >
                  Create Harness
                </Link>
              </div>
            )}

            {!isLoading && !error && data?.data && data.data.map((harness) => (
              <HarnessCard key={harness.id} harness={harness} />
            ))}
          </div>

        </div>
      </main>
    </div>
    </AuthGuard>
  );
}

interface StatCardProps {
  icon: React.ElementType;
  label: string;
  value: string;
  sublabel?: string;
  accent?: 'blue' | 'green' | 'amber';
  trend?: string;
}

function StatCard({ icon: Icon, label, value, sublabel, accent, trend }: StatCardProps): JSX.Element {
  return (
    <div
      className={cn(
        'rounded-2xl bg-white p-5',
        'border border-slate-200/80',
        'shadow-card transition-all duration-200',
        'hover:shadow-card-hover'
      )}
    >
      <div className="flex items-start justify-between">
        <div
          className={cn(
            'flex h-10 w-10 items-center justify-center rounded-xl',
            accent === 'green' && 'bg-secondary-50',
            accent === 'blue' && 'bg-primary-50',
            accent === 'amber' && 'bg-accent-50',
            !accent && 'bg-slate-100'
          )}
        >
          <Icon
            size={18}
            weight="fill"
            className={cn(
              accent === 'green' && 'text-secondary-600',
              accent === 'blue' && 'text-primary-600',
              accent === 'amber' && 'text-accent-600',
              !accent && 'text-slate-500'
            )}
          />
        </div>
        {trend && (
          <span className="rounded-full bg-secondary-50 px-2 py-0.5 text-[11px] font-semibold text-secondary-600">
            {trend}
          </span>
        )}
      </div>
      <div className="mt-4">
        <p className="text-2xl font-semibold tracking-tight text-slate-900">{value}</p>
        <p className="mt-0.5 text-sm text-slate-500">{label}</p>
        {sublabel && (
          <p className="text-xs text-slate-400">{sublabel}</p>
        )}
      </div>
    </div>
  );
}

/**
 * Loading Skeleton Components
 * Following corridor-platforms pattern for loading states
 */
function StatCardSkeleton(): JSX.Element {
  return (
    <div className="rounded-2xl bg-white p-5 border border-slate-200/80 shadow-card">
      <div className="flex items-start justify-between">
        <div className="h-10 w-10 rounded-xl bg-slate-100 animate-pulse" />
      </div>
      <div className="mt-4 space-y-2">
        <div className="h-8 w-16 bg-slate-100 rounded animate-pulse" />
        <div className="h-4 w-24 bg-slate-100 rounded animate-pulse" />
        <div className="h-3 w-20 bg-slate-100 rounded animate-pulse" />
      </div>
    </div>
  );
}

function HarnessCardSkeleton(): JSX.Element {
  return (
    <div className="rounded-2xl bg-white p-6 border border-slate-200/80 shadow-card">
      <div className="flex items-start justify-between">
        <div className="flex-1 space-y-3">
          <div className="h-6 w-48 bg-slate-100 rounded animate-pulse" />
          <div className="h-4 w-32 bg-slate-100 rounded animate-pulse" />
          <div className="flex gap-2 mt-4">
            <div className="h-6 w-20 bg-slate-100 rounded-lg animate-pulse" />
            <div className="h-6 w-20 bg-slate-100 rounded-lg animate-pulse" />
            <div className="h-6 w-20 bg-slate-100 rounded-lg animate-pulse" />
          </div>
        </div>
        <div className="h-8 w-8 bg-slate-100 rounded-lg animate-pulse" />
      </div>
    </div>
  );
}
