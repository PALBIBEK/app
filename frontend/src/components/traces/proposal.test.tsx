/**
 * A proposed change reads plainly: only the sections it changes show as a diff, and the gate
 * says in words why it passed or was refused.
 */
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { EditList, GateResult, PromptDiff, ProposalStatusPill, SearchRounds, WatchLine } from './Proposal';

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

  it('marks the saved runs a change never read', () => {
    render(
      <GateResult
        gate={{
          passed: false,
          reasons: ['it did no better on any of the 1 held-out saved runs, only on runs it learnt from'],
          better: 1,
          worse: 0,
          items: [
            { item_id: 'a', input: 'fix it', change: 'better' },
            { item_id: 'b', input: 'add a flag', change: 'same', held_out: true },
          ],
        }}
      />,
    );
    expect(screen.getAllByTestId('gate-held-out')).toHaveLength(1);
    expect(screen.getByTestId('gate').textContent).toContain('only on runs it learnt from');
  });
});

describe('ProposalStatusPill', () => {
  it('names where the change stands', () => {
    render(<ProposalStatusPill status="awaiting_approval" />);
    expect(screen.getByText('Waiting for you')).toBeTruthy();
  });
});

describe('EditList', () => {
  it('lists each edit with why, what it expects, and what could not be applied', () => {
    render(
      <EditList
        edits={[
          { action: 'add', section: 'tools', text: 'Read the error first.', old: null, reason: 'bash calls repeated' },
          { action: 'replace', section: 'role', text: 'You fix code, carefully.', old: 'You fix code.', reason: '' },
        ]}
        skipped={["edit 3: unknown action 'delete'"]}
        expected="fewer repeated calls"
      />,
    );
    const edits = screen.getAllByTestId('edit').map((e) => e.textContent);
    expect(edits).toEqual(['Adds to tools · bash calls repeatedRead the error first.', 'Changes roleYou fix code, carefully.']);
    expect(screen.getByText('fewer repeated calls')).toBeTruthy();
    expect(screen.getByTestId('skipped-edits').textContent).toBe("Not applied: edit 3: unknown action 'delete'.");
  });

  it('shows nothing for a proposal made before edits were itemized', () => {
    const { container } = render(<EditList edits={[]} skipped={[]} expected={null} />);
    expect(container.textContent).toBe('');
  });
});

describe('SearchRounds', () => {
  it('shows each candidate a search tried, what it built on, and whether it was kept', () => {
    render(
      <SearchRounds
        rounds={[
          { round: 1, parent: 0, rationale: 'test first', edits: [], experiment_id: 'e1', kept: true, why: 'scored 20 against 10' },
          { round: 2, parent: 1, rationale: null, edits: [], experiment_id: 'e2', kept: false, why: 'scored 15 against 20' },
        ]}
      />,
    );
    const rows = screen.getAllByTestId('search-round');
    expect(rows.map((r) => r.dataset.kept)).toEqual(['true', 'false']);
    expect(rows[1].textContent).toContain('edits candidate 1');
    expect(rows[0].textContent).toContain('edits the live version');
  });

  it('says nothing for a single candidate', () => {
    const { container } = render(
      <SearchRounds rounds={[{ round: 1, parent: 0, rationale: null, edits: [], experiment_id: null, kept: false, why: '' }]} />,
    );
    expect(container.textContent).toBe('');
  });
});

describe('WatchLine', () => {
  it('says a change was rolled back, and why', () => {
    render(
      <WatchLine
        watch={{
          status: 'rolled_back', previous_version: 1, checks: 0, failed_checks: 2, seen_runs: 24, rolled_back_to: 3,
          note: '50% of its live runs failed, against 10% on v1: v1 is live again as v3',
        }}
      />,
    );
    expect(screen.getByTestId('release-watch').textContent).toBe(
      'Rolled back against v1: 50% of its live runs failed, against 10% on v1: v1 is live again as v3.',
    );
  });
});
