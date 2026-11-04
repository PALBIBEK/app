/** "Propose a fix" starts a change for one issue on the chosen dataset, and links to it. */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const proposed = vi.fn(async () => ({ id: 'p1' }));
vi.mock('@/lib/api/hooks/useEvaluations', () => ({
  useDatasets: () => ({ data: [{ id: 'd1', name: 'regressions', items: 2 }] }),
  useCreateDataset: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useAddIssue: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useAddRun: () => ({ mutateAsync: vi.fn(), isPending: false }),
  usePropose: () => ({ mutateAsync: proposed, isPending: false }),
}));
vi.mock('next/link', () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import type { Issue } from '@/lib/api/types/trace.types';

import { ProposeFromIssue } from './SaveToDataset';

afterEach(cleanup);

const issue = {
  key: 'h:check:tool_errors:bash', title: 'bash calls failed', source: 'check', name: 'tool_errors', topic: 'bash',
  runs: 4, last_seen_ms: 1, agent_id: 'a1', agent_name: 'fixer', versions: [], examples: [],
} as Issue;

describe('ProposeFromIssue', () => {
  it('starts a change for the issue on the chosen dataset and links to it', async () => {
    render(<ProposeFromIssue issue={issue} />);
    fireEvent.click(screen.getByTestId('propose-from-issue'));
    fireEvent.submit(screen.getByRole('form', { name: 'Propose a fix for this issue' }));
    await waitFor(() => expect(screen.getByTestId('proposal-started').getAttribute('href')).toBe('/traces/improve/p1'));
    expect(proposed).toHaveBeenCalledWith({ dataset_id: 'd1', issue_key: issue.key, notes: 'Fix: bash calls failed' });
  });
});
