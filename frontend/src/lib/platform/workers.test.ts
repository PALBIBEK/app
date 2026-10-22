import { describe, expect, it } from 'vitest';

import type { WorkersStatus } from '@/lib/api/types/platform.types';

import { workersNotice } from './workers';

const fleet = (over: Partial<WorkersStatus>): WorkersStatus => ({
  status: 'ok', polling: 1, slots_busy: 0, slots_max: 50, backlog: 0, backlog_age_s: 0, alerts: [], ...over,
});

describe('workersNotice', () => {
  it('says nothing when workers are fine or unknown', () => {
    expect(workersNotice(fleet({}))).toBeNull();
    expect(workersNotice(null)).toBeNull();
    expect(workersNotice(undefined)).toBeNull();
  });

  it('explains a missing worker without calling it an error', () => {
    expect(workersNotice(fleet({ status: 'down', polling: 0 }))?.title).toBe('No agent worker is running');
  });

  it('names a backlog or full slots', () => {
    const behind = workersNotice(fleet({ status: 'degraded', alerts: [{ code: 'backlog', message: 'Work has waited 45s for a worker (3 queued)' }] }));
    expect(behind?.title).toBe('Agent workers are behind');
    expect(behind?.message).toContain('45s');
    const full = workersNotice(fleet({ status: 'degraded', alerts: [{ code: 'at_capacity', message: 'Every worker is at capacity (2/2 runs)' }] }));
    expect(full?.title).toBe('Agent workers are at capacity');
  });
});
