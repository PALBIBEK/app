/** A run to review says why it needs a person, shows its verdicts, and is marked in one click. */
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const mutate = vi.fn();
vi.mock('@/lib/api/hooks/useTraces', () => ({ useFeedback: () => ({ mutate, isPending: false }) }));
vi.mock('next/link', () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import { ReviewItem } from './ReviewItem';

afterEach(cleanup);

describe('ReviewItem', () => {
  it('says why, shows the verdicts, and marks the run', () => {
    render(
      <ReviewItem
        run={{
          session_id: 's', run_id: 'r1', started_ms: 1, input: 'fix it', summary: 'Answered.', why: 'checks_disagree',
          says: 'a judge passed what a check failed', verdicts: [{ name: 'answers', label: 'pass', reason: 'fine' }],
          checks_failed: ['tool_errors'],
        }}
      />,
    );
    const item = screen.getByTestId('review-run');
    expect(item.textContent).toContain('a judge passed what a check failed');
    expect(item.textContent).toContain('answers: pass — fine');
    expect(item.textContent).toContain('failed checks: tool_errors');
    fireEvent.click(screen.getByTestId('mark-bad'));
    expect(mutate).toHaveBeenCalledWith({ good: false });
  });
});
