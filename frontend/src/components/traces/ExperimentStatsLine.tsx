'use client';

/** How a version did on a dataset, in one line: answered, failing checks, judges, cost, time. */

import type { Experiment } from '@/lib/api/types/evaluation.types';
import { formatMs } from '@/lib/traces/format';
import { formatMoney } from '@/lib/utils';

export function ExperimentStatsLine({ experiment }: { experiment: Experiment }): JSX.Element {
  const s = experiment.stats;
  if (!s) return <span className="text-ink-3">{experiment.status === 'queued' ? 'waiting to start' : '—'}</span>;
  const parts = [
    `${s.answered} of ${s.items} answered`,
    s.checks_failed ? `${s.checks_failed} failing checks` : null,
    s.judge_pass || s.judge_fail ? `judges ${s.judge_pass} pass, ${s.judge_fail} fail` : null,
    s.needs_approval ? `${s.needs_approval} stopped for an approval` : null,
    s.avg_cost != null ? `${formatMoney(s.avg_cost)} a run` : null,
    s.avg_duration_ms != null ? formatMs(s.avg_duration_ms) : null,
  ].filter(Boolean);
  return <span data-testid="experiment-stats">{parts.join(' · ')}</span>;
}
