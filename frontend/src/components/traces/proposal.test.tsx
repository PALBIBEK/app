/**
 * A proposed change reads plainly: only the sections it changes show as a diff, and the gate
 * says in words why it passed or was refused.
 */
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { GateResult, PromptDiff, ProposalStatusPill } from './Proposal';

afterEach(cleanup);

describe('PromptDiff', () => {
  it('shows changed and new sections as diffs and folds the unchanged ones away', () => {
    render(
      <PromptDiff
        base={{ persona: 'coder', sections: [{ id: 'intro', text: 'You code.' }, { id: 'rules', text: 'Be brief.' }] }}
        next={{
          persona: 'coder',
          sections: [
            { id: 'intro', text: 'You code.' },
            { id: 'rules', text: 'Be brief. Run the tests first.' },
            { id: 'verify', text: 'Read test output.' },
          ],
        }}
      />,
    );
    expect(screen.getAllByTestId('changed-section').map((e) => e.getAttribute('data-section'))).toEqual(['rules', 'verify']);
    expect(screen.getByText('verify (new section)')).toBeTruthy();
    expect(screen.getByText('Unchanged: intro')).toBeTruthy();
  });
});

describe('GateResult', () => {
  it('says why a change was refused', () => {
    render(
      <GateResult
        gate={{ passed: false, reasons: ['it did worse on 1 of 2 saved runs'], better: 0, worse: 1, items: [{ item_id: 'i', input: 'fix it', change: 'worse' }] }}
      />,
    );
    expect(screen.getByTestId('gate').textContent).toContain('Refused: it did worse on 1 of 2 saved runs.');
    expect(screen.getByText('worse')).toBeTruthy();
  });
});

describe('ProposalStatusPill', () => {
  it('names where the change stands', () => {
    render(<ProposalStatusPill status="awaiting_approval" />);
    expect(screen.getByText('Waiting for you')).toBeTruthy();
  });
});
