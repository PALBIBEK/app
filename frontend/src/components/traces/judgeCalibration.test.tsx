/**
 * A judge read against people: how often it agrees, and a rubric's verdicts on recent runs
 * beside what people said of them.
 */
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('next/link', () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import { AgreementLine, PreviewResults } from './JudgeCalibration';

afterEach(cleanup);

describe('AgreementLine', () => {
  it('says how often the judge agrees with people, and warns when it is near chance', () => {
    render(
      <AgreementLine
        agreement={{ name: 'answers', runs: 4, agreement: 0.5, kappa: 0, passed_but_marked_bad: 2, failed_but_marked_good: 0 }}
      />,
    );
    const line = screen.getByTestId('judge-agreement');
    expect(line.textContent).toBe(
      'Agrees with people on 50% of 4 runs (kappa 0.00): passed 2 people marked wrong. Little better than chance: sharpen the rubric.',
    );
    expect(line.dataset.low).toBe('true');
  });

  it('asks for marked runs when there are none to compare', () => {
    render(<AgreementLine agreement={undefined} />);
    expect(screen.getByTestId('judge-agreement').textContent).toContain('mark a few runs good or bad');
  });
});

describe('PreviewResults', () => {
  it('shows each verdict and whether people agree', () => {
    render(
      <PreviewResults
        runs={[
          { session_id: 's', run_id: 'r1', input: 'fix it', verdict: 'fail', reason: 'no why', people: 'good' },
          { session_id: 's', run_id: 'r2', input: 'add it', verdict: 'pass', reason: 'ok', people: null },
        ]}
      />,
    );
    const rows = screen.getAllByTestId('preview-run').map((r) => r.textContent);
    expect(rows[0]).toContain('people marked it good');
    expect(rows[1]).not.toContain('people');
  });
});
