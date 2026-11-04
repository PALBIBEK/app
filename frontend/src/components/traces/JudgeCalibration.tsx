'use client';

/**
 * Is a judge right? Its agreement with what people said of the same runs (and Cohen's kappa,
 * agreement beyond chance), and a rubric's verdicts on recent runs before it is saved.
 */

import Link from 'next/link';

import type { JudgeAgreement, JudgePreviewRun } from '@/lib/api/types/trace.types';
import { cn } from '@/lib/utils';

/** Below this a judge agrees with people little beyond chance (Landis & Koch call 0.41–0.60 "moderate"). */
const LOW_KAPPA = 0.4;

export function AgreementLine({ agreement }: { agreement: JudgeAgreement | undefined }): JSX.Element {
  if (!agreement || !agreement.runs) {
    return (
      <span className="text-xs text-ink-3" data-testid="judge-agreement">
        No runs both judged and marked by people yet: mark a few runs good or bad to see if it agrees.
      </span>
    );
  }
  const pct = agreement.agreement != null ? Math.round(agreement.agreement * 100) : null;
  const low = agreement.kappa != null && agreement.kappa < LOW_KAPPA;
  const wrong = [
    agreement.passed_but_marked_bad ? `passed ${agreement.passed_but_marked_bad} people marked wrong` : '',
    agreement.failed_but_marked_good ? `failed ${agreement.failed_but_marked_good} people marked good` : '',
  ].filter(Boolean);
  return (
    <span className={cn('text-xs', low ? 'text-bad' : 'text-ink-2')} data-testid="judge-agreement" data-low={low}>
      Agrees with people on {pct}% of {agreement.runs} run{agreement.runs === 1 ? '' : 's'}
      {agreement.kappa != null ? ` (kappa ${agreement.kappa.toFixed(2)})` : ''}
      {wrong.length ? `: ${wrong.join(', ')}` : ''}
      {low ? '. Little better than chance: sharpen the rubric.' : ''}
    </span>
  );
}

export function PreviewResults({ runs }: { runs: JudgePreviewRun[] }): JSX.Element {
  return (
    <ul className="flex flex-col divide-y divide-line-soft rounded-lg border border-line-soft" data-testid="judge-preview">
      {runs.map((r) => {
        const agrees = r.people == null || r.verdict === 'unclear' ? null : (r.verdict === 'pass') === (r.people === 'good');
        return (
          <li key={`${r.session_id}-${r.run_id}`} className="flex flex-col gap-0.5 px-3 py-2 text-[13px]" data-testid="preview-run">
            <span className="flex flex-wrap items-baseline gap-x-2">
              <span className={cn('font-semibold', r.verdict === 'pass' ? 'text-emerald-800' : r.verdict === 'fail' ? 'text-bad' : 'text-ink-3')}>
                {r.verdict}
              </span>
              <Link href={`/traces/${r.session_id}/${r.run_id}`} className="truncate hover:underline">
                {r.input || 'Run'}
              </Link>
              {agrees != null ? (
                <span className={cn('text-xs', agrees ? 'text-ink-3' : 'text-bad')}>
                  {agrees ? `agrees with people (${r.people})` : `people marked it ${r.people}`}
                </span>
              ) : null}
            </span>
            <span className="text-ink-2">{r.reason}</span>
          </li>
        );
      })}
    </ul>
  );
}
