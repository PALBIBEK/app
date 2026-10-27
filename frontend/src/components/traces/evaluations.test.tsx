/**
 * Experiments read plainly: a version's results in one line, each replayed run with how it
 * went and its verdicts, and a replay that stopped for an approval said so.
 */
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Experiment, ExperimentItemResult } from '@/lib/api/types/evaluation.types';

vi.mock('next/link', () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import { ExperimentStatsLine } from './ExperimentStatsLine';
import { ResultCell } from './ResultCell';

afterEach(cleanup);

const experiment: Experiment = {
  id: 'e1',
  dataset_id: 'd1',
  label: 'v2',
  release_version: 2,
  composition_digest: null,
  status: 'done',
  error: null,
  created_at: '2026-10-27T10:00:00Z',
  started_at: null,
  finished_at: null,
  stats: {
    items: 4,
    finished: 4,
    answered: 3,
    failed: 0,
    needs_approval: 1,
    checks_failed: 1,
    judge_pass: 2,
    judge_fail: 1,
    avg_cost: 0.02,
    avg_duration_ms: 4200,
  },
};

function result(over: Partial<ExperimentItemResult>): ExperimentItemResult {
  return {
    item_id: 'i1',
    position: 0,
    input: 'fix the test',
    expected: null,
    status: 'done',
    error: null,
    session_id: 's1',
    run_id: 'r1',
    run_status: 'ok',
    summary: 'Ran 1 command; answered.',
    cost: 0.02,
    duration_ms: 1000,
    verdicts: [],
    ...over,
  };
}

describe('ExperimentStatsLine', () => {
  it('says how the version did in one line', () => {
    render(<ExperimentStatsLine experiment={experiment} />);
    expect(screen.getByTestId('experiment-stats').textContent).toBe(
      '3 of 4 answered · 1 failing checks · judges 2 pass, 1 fail · 1 stopped for an approval · $0.020 a run · 4.2 s',
    );
  });

  it('says an experiment has not started rather than showing zeros', () => {
    render(<ExperimentStatsLine experiment={{ ...experiment, status: 'queued', stats: null }} />);
    expect(screen.getByText('waiting to start')).toBeTruthy();
  });
});

describe('ResultCell', () => {
  it('shows how the run ended, its summary, its verdicts and a link to its trace', () => {
    render(
      <ResultCell
        result={result({ verdicts: [{ name: 'answers', source: 'judge', label: 'fail', reason: 'it guessed' }] })}
      />,
    );
    expect(screen.getByText('Answered')).toBeTruthy();
    expect(screen.getByText('Ran 1 command; answered.')).toBeTruthy();
    expect(screen.getByText('judge answers: fail').getAttribute('title')).toBe('it guessed');
    expect(screen.getByText('trace').getAttribute('href')).toBe('/traces/s1/r1');
  });

  it('says a replay stopped for an approval, with why', () => {
    render(
      <ResultCell
        result={result({ status: 'needs_approval', run_status: 'cancelled', error: 'stopped for an approval: experiments never approve' })}
      />,
    );
    expect(screen.getByText('stopped for an approval')).toBeTruthy();
    expect(screen.getByText('stopped for an approval: experiments never approve')).toBeTruthy();
  });
});
