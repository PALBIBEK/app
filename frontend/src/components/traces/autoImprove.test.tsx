/**
 * Proposing by itself: off until turned on, says what its last check found, and saves what
 * the person chose.
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const mutate = vi.fn();
let current: unknown = null;
vi.mock('@/lib/api/hooks/useEvaluations', () => ({
  useAutoImprove: () => ({ data: current }),
  useSaveAutoImprove: () => ({ mutate, isPending: false }),
}));

import { AutoImproveCard } from './AutoImproveCard';

afterEach(() => {
  cleanup();
  mutate.mockReset();
});

const datasets = [{ id: 'd1', agent_id: 'a', name: 'regressions', description: null, items: 3, experiments: 0, created_at: '' }];

describe('AutoImproveCard', () => {
  it('turns on with the chosen dataset, threshold and cooldown', () => {
    current = null;
    render(<AutoImproveCard agentId="a" datasets={datasets} />);
    expect(screen.getByTestId('auto-improve').dataset.enabled).toBe('false');
    fireEvent.click(screen.getByTestId('auto-improve-toggle'));
    expect(mutate).toHaveBeenCalledWith({ dataset_id: 'd1', enabled: true, min_runs: 5, cooldown_minutes: 360, rounds: 1 });
  });

  it('says what the last check found, and turns off', () => {
    current = {
      agent_id: 'a', dataset_id: 'd1', enabled: true, min_runs: 3, cooldown_minutes: 60, rounds: 2,
      last_attempt_at: null, last_proposal_id: null, last_note: '“bash calls failed” failed 2 runs since the last attempt',
    };
    render(<AutoImproveCard agentId="a" datasets={datasets} />);
    expect(screen.getByTestId('auto-improve-note').textContent).toBe('“bash calls failed” failed 2 runs since the last attempt.');
    fireEvent.click(screen.getByTestId('auto-improve-toggle'));
    expect(mutate).toHaveBeenCalledWith({ dataset_id: 'd1', enabled: false, min_runs: 3, cooldown_minutes: 60, rounds: 2 });
  });
});
