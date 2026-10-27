'use client';

/**
 * How a run went, in verdicts: the automatic checks (each with its reason, and a link to the
 * span it points at) and the user's own good/wrong call on the run or a step, with a note.
 */

import { Check, X } from '@phosphor-icons/react';
import { useState } from 'react';

import { Button, Card } from '@/components/ui';
import { useFeedback } from '@/lib/api/hooks/useTraces';
import type { RunTrace, TraceScore } from '@/lib/api/types/trace.types';
import { cn } from '@/lib/utils';

const CHECK_TITLE: Record<string, string> = {
  finished: 'Finished',
  tool_errors: 'Tool calls worked',
  repeated_calls: 'No repeated calls',
  approvals: 'Approvals allowed',
  tests_after_change: 'Tests after the last change',
};

export function checksOf(trace: RunTrace): TraceScore[] {
  return (trace.scores ?? []).filter((s) => s.source === 'check');
}

/** The user's feedback on the run (``spanId`` null) or on one span. */
export function myFeedback(trace: RunTrace, spanId: string | null): TraceScore | undefined {
  return (trace.scores ?? []).find((s) => s.source === 'person' && s.mine && s.span_id === spanId);
}

export function ChecksCard({ trace, onOpen }: { trace: RunTrace; onOpen: (spanId: string) => void }): JSX.Element | null {
  const checks = checksOf(trace);
  if (!checks.length) return null;
  const failed = checks.filter((c) => c.label === 'fail').length;
  return (
    <Card className="flex flex-col gap-2.5 px-4 py-3.5" aria-label="Checks" data-testid="checks-card">
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold">Checks</h2>
        <span className={cn('text-xs', failed ? 'text-bad' : 'text-ink-3')}>
          {failed ? `${failed} of ${checks.length} failed` : `all ${checks.length} passed`}
        </span>
      </div>
      <ul className="flex flex-col gap-2">
        {checks.map((c) => {
          const ok = c.label === 'pass';
          return (
            <li key={c.id} className="flex gap-2 text-[13px]" data-testid="check" data-name={c.name} data-label={c.label}>
              <span
                aria-hidden="true"
                className={cn('mt-0.5 flex h-4 w-4 flex-none items-center justify-center rounded-full', ok ? 'bg-emerald-50 text-emerald-700' : 'bg-red-50 text-red-700')}
              >
                {ok ? <Check size={11} weight="bold" /> : <X size={11} weight="bold" />}
              </span>
              <span className="flex min-w-0 flex-col">
                <span className="font-medium">
                  <span className="sr-only">{ok ? 'Passed: ' : 'Failed: '}</span>
                  {CHECK_TITLE[c.name] ?? c.name.replace(/_/g, ' ')}
                </span>
                {c.reason ? <span className="text-ink-3">{c.reason}</span> : null}
                {!ok && c.span_id ? (
                  <button type="button" onClick={() => onOpen(c.span_id!)} className="w-fit text-xs text-accent-600 hover:underline">
                    Show where
                  </button>
                ) : null}
              </span>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

/**
 * Good / wrong buttons for the run or a step. Choosing the other one replaces the verdict;
 * choosing the same one again takes it back. "Wrong" asks what went wrong (optional): that
 * note is what a later fix is made from.
 */
export function FeedbackButtons({
  trace,
  spanId = null,
  noun,
}: {
  trace: RunTrace;
  spanId?: string | null;
  noun: 'run' | 'step';
}): JSX.Element {
  const mine = myFeedback(trace, spanId);
  const { mutate, isPending } = useFeedback(trace.session_id, trace.run_id);
  const [asking, setAsking] = useState(false);
  const [note, setNote] = useState('');
  const say = (good: boolean | null, comment?: string): void => mutate({ good, spanId, comment });
  const good = mine?.label === 'good';
  const bad = mine?.label === 'bad';
  return (
    <div className="flex flex-col gap-2" data-testid={`feedback-${noun}`}>
      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          variant={good ? 'primary' : 'secondary'}
          aria-pressed={good}
          disabled={isPending}
          onClick={() => {
            setAsking(false);
            say(good ? null : true);
          }}
        >
          {noun === 'run' ? 'Good answer' : 'Good step'}
        </Button>
        <Button
          size="sm"
          variant={bad ? 'danger' : 'secondary'}
          aria-pressed={bad}
          disabled={isPending}
          onClick={() => (bad ? say(null) : setAsking(true))}
        >
          {noun === 'run' ? 'Wrong answer' : 'Wrong step'}
        </Button>
        {mine?.comment ? <span className="text-xs text-ink-3">“{mine.comment}”</span> : null}
      </div>
      {asking ? (
        <form
          className="flex flex-col gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            say(false, note);
            setAsking(false);
            setNote('');
          }}
        >
          <label className="text-xs text-ink-3" htmlFor={`why-${noun}-${spanId ?? 'run'}`}>
            What went wrong? (optional)
          </label>
          <textarea
            id={`why-${noun}-${spanId ?? 'run'}`}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={2}
            maxLength={2000}
            className="rounded-lg border border-line-strong bg-surface px-2.5 py-1.5 text-[13.5px]"
          />
          <div className="flex gap-2">
            <Button size="sm" variant="danger" type="submit" disabled={isPending}>
              Mark wrong
            </Button>
            <Button size="sm" variant="ghost" type="button" onClick={() => setAsking(false)}>
              Cancel
            </Button>
          </div>
        </form>
      ) : null}
    </div>
  );
}
