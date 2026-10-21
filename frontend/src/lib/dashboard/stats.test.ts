import { describe, expect, it } from 'vitest';

import { dashboardStats } from './stats';

const now = new Date(2026, 9, 21, 15, 0);
const at = (h: number, dayOffset = 0) => new Date(2026, 9, 21 + dayOffset, h).toISOString();

describe('dashboardStats', () => {
  it('counts live harnesses, all sessions and today\'s', () => {
    const s = dashboardStats(
      [{ bind: { bind_status: 'bound' } }, { bind: { bind_status: 'unbound' } }, {}],
      { total: 7, data: [{ created_at: at(14) }, { created_at: at(9) }, { created_at: at(23, -1) }] },
      now,
    );
    expect(s).toEqual({ harnesses: 3, live: 1, sessions: 7, today: 2, todayCapped: false });
  });

  it('marks today as capped when the whole page is from today and more exist', () => {
    const s = dashboardStats([], { total: 150, data: [{ created_at: at(14) }, { created_at: at(13) }] }, now);
    expect(s.today).toBe(2);
    expect(s.todayCapped).toBe(true);
  });

  it('is all zeros before data arrives', () => {
    expect(dashboardStats(undefined, undefined, now)).toEqual({
      harnesses: 0, live: 0, sessions: 0, today: 0, todayCapped: false,
    });
  });
});
