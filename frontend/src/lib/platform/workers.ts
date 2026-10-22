/**
 * What to tell a user about the agent workers. Missing or busy workers never block a run (work
 * waits in Temporal and starts once a worker is free), so this is a notice, not an error.
 */

import type { WorkersStatus } from '@/lib/api/types/platform.types';

export interface WorkersNotice {
  title: string;
  message: string;
}

export function workersNotice(workers: WorkersStatus | null | undefined): WorkersNotice | null {
  if (!workers || workers.status === 'ok') return null;
  if (workers.status === 'down') {
    return {
      title: 'No agent worker is running',
      message: 'New sessions and messages wait until one starts; nothing is lost.',
    };
  }
  const first = workers.alerts[0];
  return {
    title: first?.code === 'at_capacity' ? 'Agent workers are at capacity' : 'Agent workers are behind',
    message: `${first?.message ?? 'Work is waiting for a worker'}. It starts as soon as a worker is free.`,
  };
}
