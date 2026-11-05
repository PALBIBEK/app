import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { TaskPanel } from './TaskPanel';
import { TaskCard } from './tools/TaskCard';

const TASK = {
  goal: 'Find the cheapest paper towels',
  steps: [
    { title: 'Search Shop A', status: 'done' as const, note: '£3.00 for 6' },
    { title: 'Search Shop B', status: 'doing' as const },
  ],
  found: [{ label: 'Shop A, 6 rolls', value: '£3.00', source: 'https://shop-a.example/towels' }],
};

describe('the task panel', () => {
  it('shows the goal, each step with its status, and what was found', () => {
    render(<TaskPanel task={TASK} />);

    expect(screen.getByText('Find the cheapest paper towels')).toBeTruthy();
    expect(screen.getByTestId('task-progress').textContent).toBe('1 of 2 done');
    expect(screen.getAllByTestId('task-step').map((s) => [s.dataset.status, s.textContent])).toEqual([
      ['done', 'Search Shop A£3.00 for 6'],
      ['doing', 'Search Shop B'],
    ]);
    expect(screen.getByRole('img', { name: 'In progress' })).toBeTruthy();
    const found = screen.getByTestId('task-found');
    expect(found.textContent).toBe('Shop A, 6 rolls£3.00');
    expect(found.querySelector('a')?.getAttribute('href')).toBe('https://shop-a.example/towels');
  });

  it('folds away, keeping how far along it is', () => {
    render(<TaskPanel task={TASK} />);
    fireEvent.click(screen.getByRole('button', { name: /Task/ }));

    expect(screen.queryByTestId('task-step')).toBeNull();
    expect(screen.getByTestId('task-progress').textContent).toBe('1 of 2 done');
  });

  it('is not there when the agent shows no task', () => {
    const { container } = render(<TaskPanel task={null} />);
    expect(container.innerHTML).toBe('');
  });
});

describe('an update_task call in the conversation', () => {
  it('is one line saying how far along the task is', () => {
    render(<TaskCard tool={{ id: 'c1', name: 'update_task', status: 'success', output: { task: TASK } }} />);
    expect(screen.getByTestId('task-card').textContent).toBe('Updated the task: 1 of 2 done');
  });

  it('reads like any refused call when refused', () => {
    render(<TaskCard tool={{ id: 'c1', name: 'update_task', status: 'error', error: 'steps: too many' }} />);
    expect(screen.getByTestId('tool-card')).toBeTruthy();
  });
});
