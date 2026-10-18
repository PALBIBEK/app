/**
 * HarnessCard - Premium Edition
 *
 * Design: Refined card with subtle shadows and elegant hover states
 * Icons: Phosphor
 */

'use client';

import Link from 'next/link';
import {
  Play,
  PencilSimple,
  ClockCounterClockwise,
  CircleDashed,
  CheckCircle,
  DotsThree,
  ArrowRight,
  Cpu,
} from '@phosphor-icons/react';
import { cn, formatRelativeTime } from '@/lib/utils';
import type { Harness } from '@/lib/api/types/harness.types';
import { usePlatformStatus } from '@/lib/api/hooks/usePlatformStatus';

interface HarnessCardProps {
  harness: Harness;
}

export function HarnessCard({ harness }: HarnessCardProps): JSX.Element {
  const bindStatus = harness.bind?.bind_status ?? 'unbound';
  const isBound = bindStatus === 'bound';
  const { data: platform, isLoading: platformLoading, isFetching } = usePlatformStatus();
  const platformReady = !platformLoading && !isFetching && platform?.run_allowed === true;
  const testEnabled = isBound && platformReady;
  const testTitle = !isBound
    ? 'Bind a composition before testing'
    : !platformReady
      ? 'AI platform unavailable — Test is disabled'
      : 'Test harness';

  const badgeLabel =
    bindStatus === 'bound' ? 'Bound' : bindStatus === 'stale' ? 'Stale' : 'Draft';
  const badgeActive = bindStatus === 'bound';

  return (
    <div
      className={cn(
        'group relative rounded-2xl bg-white p-5',
        'border border-slate-200/80',
        'shadow-card transition-all duration-200',
        'hover:border-slate-300 hover:shadow-card-hover'
      )}
    >
      <div className="grid grid-cols-12 gap-5">
        {/* Left: Main info */}
        <div className="col-span-7 space-y-3">
          {/* Title row */}
          <div className="flex items-start gap-3">
            {/* Icon */}
            <div
              className={cn(
                'flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl',
                badgeActive
                  ? 'bg-gradient-to-br from-secondary-50 to-secondary-100'
                  : 'bg-slate-100'
              )}
            >
              <Cpu
                size={18}
                weight="fill"
                className={badgeActive ? 'text-secondary-600' : 'text-slate-500'}
              />
            </div>

            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <h3 className="text-[15px] font-semibold tracking-tight text-slate-900">
                  {harness.name}
                </h3>
                <span
                  className={cn(
                    'inline-flex items-center gap-1 rounded-full px-2 py-0.5',
                    'text-[11px] font-medium',
                    badgeActive
                      ? 'bg-secondary-50 text-secondary-700 ring-1 ring-inset ring-secondary-200'
                      : 'bg-slate-100 text-slate-500 ring-1 ring-inset ring-slate-200'
                  )}
                >
                  {badgeActive ? (
                    <CheckCircle size={10} weight="fill" />
                  ) : (
                    <CircleDashed size={10} />
                  )}
                  {badgeLabel}
                </span>
              </div>

              {/* Meta row */}
              <div className="mt-1.5 flex items-center gap-3 text-[13px] text-slate-500">
                <span className="font-medium text-slate-600">{harness.model}</span>
                <span className="h-1 w-1 rounded-full bg-slate-300" />
                <span>{harness.tools.length} tools</span>
                <span className="h-1 w-1 rounded-full bg-slate-300" />
                <span className="capitalize">{harness.mode}</span>
              </div>
            </div>
          </div>

          {/* Stats row */}
          <div className="flex items-center gap-4 pl-[52px]">
            <div className="flex items-center gap-1.5 text-[13px] text-slate-500">
              <ClockCounterClockwise size={14} className="text-slate-400" />
              <span>Updated {formatRelativeTime(new Date(harness.updated_at))}</span>
            </div>
          </div>
        </div>

        {/* Right: Actions */}
        <div className="col-span-5 flex items-center justify-end gap-2">
          {/* Test button - primary action (gated by bind ∧ D2 platform status) */}
          {testEnabled ? (
            <Link
              href={`/harness/${harness.id}/test`}
              title={testTitle}
              className={cn(
                'inline-flex items-center gap-2 rounded-xl px-4 py-2.5',
                'text-sm font-medium text-white',
                'bg-gradient-to-br from-primary-500 to-primary-600',
                'shadow-sm shadow-primary-500/20',
                'transition-all duration-150',
                'hover:from-primary-600 hover:to-primary-700 hover:shadow-md hover:shadow-primary-500/25',
                'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500/40',
                'active:scale-[0.98]'
              )}
            >
              <Play size={14} weight="fill" />
              Test
            </Link>
          ) : (
            <button
              type="button"
              disabled
              title={testTitle}
              aria-disabled="true"
              className={cn(
                'inline-flex items-center gap-2 rounded-xl px-4 py-2.5',
                'text-sm font-medium text-slate-400',
                'bg-slate-100 cursor-not-allowed'
              )}
            >
              <Play size={14} weight="fill" />
              Test
            </button>
          )}

          {/* Edit button */}
          <Link
            href={`/harness/${harness.id}`}
            className={cn(
              'inline-flex items-center gap-2 rounded-xl px-3.5 py-2.5',
              'text-sm font-medium text-slate-700',
              'bg-white border border-slate-200',
              'shadow-sm',
              'transition-all duration-150',
              'hover:bg-slate-50 hover:border-slate-300',
              'focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-500/20',
              'active:scale-[0.98]'
            )}
          >
            <PencilSimple size={14} />
            Edit
          </Link>

          {/* More button */}
          <button
            className={cn(
              'flex h-10 w-10 items-center justify-center rounded-xl',
              'text-slate-400',
              'border border-slate-200 bg-white',
              'transition-all duration-150',
              'hover:bg-slate-50 hover:text-slate-600 hover:border-slate-300',
              'focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-500/20'
            )}
            aria-label="More actions"
          >
            <DotsThree size={18} weight="bold" />
          </button>
        </div>
      </div>

      {/* Hover arrow indicator */}
      <div
        className={cn(
          'absolute right-4 top-1/2 -translate-y-1/2',
          'opacity-0 transition-all duration-200',
          'group-hover:opacity-100 group-hover:translate-x-1'
        )}
      >
        <ArrowRight size={16} className="text-slate-300" />
      </div>
    </div>
  );
}
