'use client';

/**
 * An `update_task` call in the conversation: one line saying how far along the task is. The
 * task itself is in the rail (../TaskPanel), where it stays in view.
 */

import { CircleNotch, ListChecks } from '@phosphor-icons/react';

import type { ToolCallView } from '@/lib/api/types/session.types';
import { progress, taskOfCall } from '@/lib/session/task';

import { ToolCard } from '../parts';

export function TaskCard({ tool }: { tool: ToolCallView }): JSX.Element {
  const task = taskOfCall(tool);
  if (tool.status === 'error' || (tool.status === 'success' && !task)) return <ToolCard tool={tool} />;
  return (
    <p data-testid="task-card" className="flex items-center gap-2 rounded-[10px] border border-line bg-surface px-3 py-2 text-[13px] text-ink-2">
      {task ? (
        <ListChecks size={15} className="shrink-0" aria-hidden="true" />
      ) : (
        <CircleNotch size={14} className="shrink-0 animate-spin text-accent-600" aria-hidden="true" />
      )}
      <span className="min-w-0 truncate">{task ? `Updated the task: ${progress(task)}` : 'Updating the task…'}</span>
    </p>
  );
}
