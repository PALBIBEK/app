/**
 * The trace view renders a run so a person can read it: the header names how it ended and
 * where the time went, the transcript shows what happened, the tree marks the failure.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { sampleRun } from '@/lib/traces/fixture';

vi.mock('next/link', () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import { RunHeader } from './RunHeader';
import { SpanTree } from './SpanTree';
import { Transcript } from './Transcript';

function wrap(ui: React.ReactElement): ReturnType<typeof render> {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, enabled: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

afterEach(cleanup);

describe('RunHeader', () => {
  it('names the outcome, the agent version and where the time went', () => {
    wrap(<RunHeader trace={sampleRun()} />);
    expect(screen.getByText('Answered')).toBeTruthy();
    expect(screen.getByTestId('run-input').textContent).toBe('Fix the date test');
    expect(screen.getByText(/^v2/)).toBeTruthy();
    expect(screen.getByRole('img').getAttribute('aria-label')).toBe(
      'Model 8.0 s, Tools 2.1 s, Waiting on you 14.0 s, Other 15.9 s',
    );
  });

  it('says why a run failed', () => {
    wrap(<RunHeader trace={sampleRun({ status: 'error', reason: 'GatewayError: model unavailable' })} />);
    expect(screen.getByText('Failed')).toBeTruthy();
    expect(screen.getByTestId('run-reason').textContent).toBe('GatewayError: model unavailable');
  });
});

describe('Transcript', () => {
  it('reads as the ask, the looking around, the approved command and the answer', () => {
    const onOpen = vi.fn();
    wrap(<Transcript trace={sampleRun()} onOpen={onOpen} />);
    expect(screen.getByTestId('transcript-input').textContent).toContain('Fix the date test');
    expect(screen.getByTestId('transcript-explore').textContent).toContain('Looked at 2 things');
    const bash = screen.getByTestId('transcript-tool');
    expect(bash.getAttribute('data-status')).toBe('error');
    expect(within(bash).getByText('exit 1')).toBeTruthy();
    expect(within(bash).getByTestId('transcript-approval').textContent).toBe('allowed by you after 14.0 s');
    expect(within(bash).getByText('1 failed, 41 passed')).toBeTruthy();
    expect(screen.getByTestId('transcript-answer').textContent).toContain('Fixed it.');
    expect(screen.getByText(/Step 2 · 17.0 s, 14.0 s of it waiting on you/)).toBeTruthy();
    fireEvent.click(within(bash).getByText('Details'));
    expect(onOpen).toHaveBeenCalledWith('t3');
  });
});

describe('SpanTree', () => {
  it('draws every span, marks the failure and the slowest call, and folds a step', () => {
    const onSelect = vi.fn();
    wrap(<SpanTree trace={sampleRun()} selectedId="t3" onSelect={onSelect} now={40_000} />);
    const rows = screen.getAllByTestId('span-row');
    expect(rows).toHaveLength(10);
    const failed = rows.find((r) => r.getAttribute('data-status') === 'error');
    expect(failed && within(failed).getByText('failed')).toBeTruthy();
    expect(screen.getByText('slowest')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Fold step 2' }));
    expect(screen.getAllByTestId('span-row')).toHaveLength(8);
    expect(screen.getByText('failure inside')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /^Agent ?reviewer/ }));
    expect(onSelect).toHaveBeenCalledWith('run');
  });
});
