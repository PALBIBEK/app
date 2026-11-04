'use client';

/**
 * A proposed change, read plainly: where it stands, what in the prompt it changes, and the
 * gate's decision with its reasons.
 */

import { DiffView } from '@/components/traces/DiffView';
import { StatusPill, type StatusKind } from '@/components/ui';
import type { Gate, PromptEdit, PromptView, ProposalStatus } from '@/lib/api/types/evaluation.types';
import { cn } from '@/lib/utils';

export const PROPOSAL_STATUS: Record<ProposalStatus, { label: string; kind: StatusKind; says: string }> = {
  drafting: { label: 'Learning', kind: 'running', says: 'Reading how the runs went wrong and drafting a change.' },
  evaluating: { label: 'Measuring', kind: 'running', says: 'Replaying the saved runs on the live version and on the change.' },
  awaiting_approval: { label: 'Waiting for you', kind: 'awaiting', says: 'It did better and no worse. Approve it to publish it as the next version.' },
  refused: { label: 'Refused by the gate', kind: 'stopped', says: 'It did not do better on the saved runs, so it never went live.' },
  no_change: { label: 'Nothing to change', kind: 'idle', says: 'The failures do not point to the prompt.' },
  failed: { label: 'Failed', kind: 'failed', says: 'It could not be finished.' },
  approved: { label: 'Published', kind: 'done', says: 'It is the live version now.' },
  rejected: { label: 'Rejected', kind: 'stopped', says: 'Dismissed; nothing changed.' },
};

export function ProposalStatusPill({ status }: { status: ProposalStatus }): JSX.Element {
  const s = PROPOSAL_STATUS[status] ?? PROPOSAL_STATUS.failed;
  return <StatusPill kind={s.kind} label={s.label} />;
}

/** The change as the reflection made it: each edit with why, what it expects, and the edits it
 * asked for that could not be applied. */
export function EditList({
  edits,
  skipped,
  expected,
}: {
  edits: PromptEdit[];
  skipped: string[];
  expected: string | null;
}): JSX.Element | null {
  if (!edits.length && !skipped.length && !expected) return null;
  return (
    <div className="flex flex-col gap-2.5" data-testid="edit-list">
      {edits.length ? (
        <ol className="flex flex-col gap-2">
          {edits.map((e, n) => (
            <li key={n} className="flex flex-col gap-0.5 text-[13px]" data-testid="edit">
              <span>
                <span className="font-medium">{e.action === 'replace' ? 'Changes' : 'Adds to'}</span>{' '}
                <span className="font-mono text-ink-2">{e.section}</span>
                {e.reason ? <span className="text-ink-3"> · {e.reason}</span> : null}
              </span>
              <span className="whitespace-pre-wrap text-ink-2">{e.text}</span>
            </li>
          ))}
        </ol>
      ) : null}
      {expected ? (
        <p className="text-[13px] text-ink-2">
          <span className="font-medium text-ink">Expected: </span>
          {expected}
        </p>
      ) : null}
      {skipped.length ? (
        <p className="text-xs text-ink-3" data-testid="skipped-edits">
          Not applied: {skipped.join('; ')}.
        </p>
      ) : null}
    </div>
  );
}

/** The prompt, section by section: unchanged sections fold away, changed ones show as a diff. */
export function PromptDiff({ base, next }: { base: PromptView; next: PromptView }): JSX.Element {
  const before = new Map(base.sections.map((s) => [s.id, s.text]));
  const rows = next.sections.map((s) => ({ id: s.id, before: before.get(s.id) ?? null, after: s.text }));
  const same = rows.filter((r) => r.before === r.after);
  return (
    <div className="flex flex-col gap-3" data-testid="prompt-diff">
      {base.persona !== next.persona ? (
        <DiffView change={{ path: 'persona', removed: base.persona, added: next.persona }} />
      ) : null}
      {rows
        .filter((r) => r.before !== r.after)
        .map((r) => (
          <div key={r.id} data-testid="changed-section" data-section={r.id}>
            <DiffView change={{ path: r.before == null ? `${r.id} (new section)` : r.id, removed: r.before ?? '', added: r.after }} />
          </div>
        ))}
      {same.length ? (
        <p className="text-[12.5px] text-ink-3">
          Unchanged: {same.map((r) => r.id).join(', ')}
        </p>
      ) : null}
    </div>
  );
}

const CHANGE: Record<string, string> = {
  better: 'bg-emerald-50 text-emerald-800',
  worse: 'bg-red-50 text-red-800',
  same: 'bg-slate-100 text-slate-700',
  unknown: 'bg-slate-100 text-slate-600',
};

/** The gate's decision: passed or refused, why, and how each saved run moved. */
export function GateResult({ gate }: { gate: Gate }): JSX.Element {
  return (
    <div className="flex flex-col gap-3" data-testid="gate" data-passed={gate.passed}>
      <p className={cn('text-[14px] font-medium', gate.passed ? 'text-emerald-800' : 'text-bad')}>
        {gate.passed ? 'Passed the gate: ' : 'Refused: '}
        {gate.reasons.join('; ')}.
      </p>
      {gate.items.length ? (
        <ul className="flex flex-col divide-y divide-line-soft rounded-lg border border-line-soft">
          {gate.items.map((i) => (
            <li key={i.item_id} className="flex items-center gap-3 px-3 py-2">
              <span className="line-clamp-1 flex-1 text-[13px]">{i.input}</span>
              {i.held_out ? (
                <span className="text-xs text-violet-800" title="A proposed change never reads this run" data-testid="gate-held-out">
                  held out
                </span>
              ) : null}
              <span className={cn('rounded-full px-2 py-px text-xs', CHANGE[i.change] ?? CHANGE.unknown)}>
                {i.change === 'unknown' ? 'not finished' : i.change}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
