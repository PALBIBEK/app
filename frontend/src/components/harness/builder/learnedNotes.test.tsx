/**
 * The notes an agent learnt show in its prompt editor after the person's sections, and the
 * person can edit or remove them.
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Manifest } from '@/lib/api/types/manifest.types';

import { PromptTab } from './tabs';

afterEach(cleanup);

const manifest = {
  prompt: {
    persona: 'coder',
    sections: [{ id: 'intro', text: 'You fix code.' }],
    learned: [{ id: 'note-00001', text: 'Run the tests first.' }],
  },
} as unknown as Manifest;

describe('PromptTab learnt notes', () => {
  it('lists them and removes one; none left is no list', () => {
    const onChange = vi.fn();
    render(<PromptTab m={manifest} onChange={onChange} />);
    expect(screen.getByTestId('learned-notes').textContent).toContain('note-00001');
    fireEvent.click(screen.getByLabelText('Remove note note-00001'));
    expect(onChange).toHaveBeenCalledWith({ ...manifest, prompt: { ...manifest.prompt, learned: null } });
  });

  it('shows nothing when the agent has learnt nothing', () => {
    render(<PromptTab m={{ ...manifest, prompt: { ...manifest.prompt, learned: null } }} onChange={vi.fn()} />);
    expect(screen.queryByTestId('learned-notes')).toBeNull();
  });
});
