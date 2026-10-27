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

const mutate = vi.fn();
vi.mock('@/lib/api/hooks/useTraces', async (orig) => ({
  ...(await orig<typeof import('@/lib/api/hooks/useTraces')>()),
  useFeedback: () => ({ mutate, isPending: false }),
}));

import { ChecksCard, FeedbackButtons } from './Scores';

describe('Scores', () => {
  it('lists the checks with their reasons and links a failure to its span', () => {
    const onOpen = vi.fn();
    wrap(<ChecksCard trace={sampleRun()} onOpen={onOpen} />);
    expect(screen.getByText('1 of 2 failed')).toBeTruthy();
    const failing = screen.getAllByTestId('check').find((c) => c.getAttribute('data-label') === 'fail')!;
    expect(failing.textContent).toContain('Tool calls worked');
    expect(failing.textContent).toContain('1 of 3 tool calls failed');
    fireEvent.click(within(failing).getByText('Show where'));
    expect(onOpen).toHaveBeenCalledWith('t3');
  });

  it('asks what went wrong before marking a run wrong, and takes a verdict back', () => {
    mutate.mockClear();
    const run = sampleRun();
    const { rerender } = wrap(<FeedbackButtons trace={run} noun="run" />);
    fireEvent.click(screen.getByRole('button', { name: 'Wrong answer' }));
    fireEvent.change(screen.getByLabelText('What went wrong? (optional)'), { target: { value: 'edited the wrong file' } });
    fireEvent.click(screen.getByRole('button', { name: 'Mark wrong' }));
    expect(mutate).toHaveBeenCalledWith({ good: false, spanId: null, comment: 'edited the wrong file' });

    const judged = { ...run, scores: [...run.scores, { id: 'f', span_id: null, name: 'feedback', source: 'person', label: 'good', value: 1, reason: null, comment: null, evaluator: 'person', mine: true, created_ms: 2 }] };
    const client = new QueryClient();
    rerender(<QueryClientProvider client={client}><FeedbackButtons trace={judged} noun="run" /></QueryClientProvider>);
    const good = screen.getByRole('button', { name: 'Good answer' });
    expect(good.getAttribute('aria-pressed')).toBe('true');
    fireEvent.click(good);
    expect(mutate).toHaveBeenLastCalledWith({ good: null, spanId: null, comment: undefined });
  });
});
