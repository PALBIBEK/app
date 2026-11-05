'use client';

/**
 * The task the agent is working on, beside the conversation: what it was asked, its plan
 * step by step, and what it has found (`update_task`, ../../lib/session/task). Most sessions
 * never show one, and then there is no panel.
 */

import { useState } from 'react';
import { CaretDown, CaretRight, CheckCircle, Circle, CircleNotch, MinusCircle, XCircle } from '@phosphor-icons/react';

import { Card } from '@/components/ui';
import { shortUrl } from '@/lib/session/browserSteps';
import { progress, type StepStatus, type TaskView } from '@/lib/session/task';
import { cn } from '@/lib/utils';

const STEP: Record<StepStatus, { icon: JSX.Element; label: string }> = {
  todo: { icon: <Circle size={15} className="text-ink-3" />, label: 'To do' },
  doing: { icon: <CircleNotch size={15} className="animate-spin text-accent-600" />, label: 'In progress' },
  done: { icon: <CheckCircle size={15} weight="fill" className="text-good" />, label: 'Done' },
  failed: { icon: <XCircle size={15} weight="fill" className="text-bad" />, label: 'Failed' },
  skipped: { icon: <MinusCircle size={15} className="text-ink-3" />, label: 'Skipped' },
};

export function TaskPanel({ task }: { task: TaskView | null }): JSX.Element | null {
  const [open, setOpen] = useState(true);
  if (!task) return null;
  return (
    <Card className="flex flex-col gap-3 px-[18px] py-4" data-testid="rail-task">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} className="flex items-baseline justify-between gap-3 text-left">
        <h2 className="inline-flex items-center gap-1.5 text-sm font-semibold">
          {open ? <CaretDown size={12} /> : <CaretRight size={12} />} Task
        </h2>
        <span data-testid="task-progress" className="text-[12.5px] text-ink-3">
          {progress(task)}
        </span>
      </button>
      {open ? (
        <>
          <p className="text-[13.5px] font-medium leading-snug">{task.goal}</p>
          {task.steps.length ? (
            <ol aria-label="Steps" className="flex flex-col gap-1.5">
              {task.steps.map((step, i) => (
                <li key={i} data-testid="task-step" data-status={step.status} className="flex items-start gap-2 text-[13px]">
                  <span className="mt-0.5 shrink-0" role="img" aria-label={STEP[step.status].label}>
                    {STEP[step.status].icon}
                  </span>
                  <span className="min-w-0">
                    <span className={cn(step.status === 'skipped' && 'text-ink-3 line-through', step.status === 'doing' && 'font-medium')}>{step.title}</span>
                    {step.note ? <span className="block text-[12.5px] text-ink-2">{step.note}</span> : null}
                  </span>
                </li>
              ))}
            </ol>
          ) : null}
          {task.found.length ? (
            <div className="flex flex-col gap-1.5 border-t border-line-soft pt-3">
              <h3 className="text-[12.5px] font-medium text-ink-3">Found so far</h3>
              <ul className="flex flex-col gap-1">
                {task.found.map((item, i) => (
                  <li key={i} data-testid="task-found" className="flex items-baseline justify-between gap-3 text-[13px]">
                    <span className="min-w-0 truncate" title={item.label}>
                      {item.source ? (
                        <a href={item.source} target="_blank" rel="noopener noreferrer nofollow" className="hover:text-accent-700 hover:underline" title={shortUrl(item.source)}>
                          {item.label}
                        </a>
                      ) : (
                        item.label
                      )}
                    </span>
                    <span className="tabular shrink-0 font-mono text-[12.5px]">{item.value}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </>
      ) : null}
    </Card>
  );
}
