'use client';

import { Info, WarningCircle } from '@phosphor-icons/react';

import { usePlatformStatus } from '@/lib/api/hooks/usePlatformStatus';
import { workersNotice } from '@/lib/platform/workers';
import { cn } from '@/lib/utils';

const REASON_COPY: Record<string, string> = {
  draining: 'AI supervisor is draining',
  database: 'AI database is unavailable',
  catalog: 'AI catalog is not ready',
  gateway: 'AI model gateway is not ready',
  memory: 'AI memory service is not ready',
  credentials: 'AI credentials are not configured',
  routes: 'AI gateway routes are invalid',
  provider: 'AI gateway provider is unavailable',
  auth: 'AI service auth is not configured',
  unreachable: 'AI supervisor is unreachable',
  not_configured: 'AI platform is not configured',
  timeout: 'AI readiness check timed out',
  sandbox: 'AI sandbox provider is unavailable',
  upstream_error: 'AI platform reported an error',
  not_ready: 'AI platform is not ready',
};

export function AiPlatformBanner({ className }: { className?: string }): JSX.Element | null {
  const { data, isLoading, isError } = usePlatformStatus();

  if (isLoading || isError || !data) {
    return null;
  }

  if (data.run_allowed) {
    // Runs are allowed, but they may wait: no worker, or workers behind.
    const notice = workersNotice(data.workers);
    if (!notice) return null;
    return (
      <div
        role="status"
        data-testid="workers-banner"
        className={cn('flex items-start gap-3 rounded-xl border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-900', className)}
      >
        <Info size={18} weight="fill" className="mt-0.5 flex-shrink-0 text-sky-600" />
        <div>
          <p className="font-semibold">{notice.title}</p>
          <p className="mt-0.5 text-sky-800">{notice.message}</p>
        </div>
      </div>
    );
  }

  const reason = data.components.supervisor?.reason ?? 'not_ready';
  const message = REASON_COPY[reason] ?? REASON_COPY.not_ready;

  return (
    <div
      role="status"
      className={cn(
        'flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900',
        className
      )}
    >
      <WarningCircle size={18} weight="fill" className="mt-0.5 flex-shrink-0 text-amber-600" />
      <div>
        <p className="font-semibold">AI platform unavailable</p>
        <p className="mt-0.5 text-amber-800">{message}. Test and Run stay disabled until it recovers.</p>
      </div>
    </div>
  );
}
