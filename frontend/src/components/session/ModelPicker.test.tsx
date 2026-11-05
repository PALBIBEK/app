/**
 * The model picker offers what can run now, by vendor; keeps the session's own model listed
 * when it is not offered; and points to Settings when a vendor is not ready.
 */
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { ModelChoices } from '@/lib/api/types/model.types';

import { groupByVendor, ModelPicker, SETTINGS_HREF } from './ModelPicker';

afterEach(cleanup);

const CHOICES: ModelChoices = {
  models: [
    { id: 'gateway/deepseek-flash', label: 'deepseek-flash', vendor: 'deepseek' },
    { id: 'gateway/local/qwen3:8b', label: 'local/qwen3:8b', vendor: 'local' },
    { id: 'gateway/glm-5.2', label: 'glm-5.2', vendor: 'zai' },
  ],
  providers: [
    { id: 'anthropic', label: 'Anthropic', ready: false },
    { id: 'deepseek', label: 'DeepSeek', ready: true },
    { id: 'zai', label: 'Z.ai (GLM)', ready: true },
    { id: 'local', label: 'Local', ready: true },
  ],
};

function picker(over: Partial<Parameters<typeof ModelPicker>[0]> = {}) {
  const onChange = vi.fn();
  render(
    <ModelPicker id="m" value="gateway/deepseek-flash" current="gateway/deepseek-flash" choices={CHOICES} onChange={onChange} {...over} />,
  );
  return { onChange, select: screen.getByLabelText('Model') as HTMLSelectElement };
}

describe('groupByVendor', () => {
  it('groups models under their vendor, in the vendors’ order, leaving out vendors with none', () => {
    const groups = groupByVendor(CHOICES);
    expect(groups.map((g) => [g.label, g.models.map((m) => m.id)])).toEqual([
      ['DeepSeek', ['gateway/deepseek-flash']],
      ['Z.ai (GLM)', ['gateway/glm-5.2']],
      ['Local', ['gateway/local/qwen3:8b']],
    ]);
  });

  it('keeps a model whose vendor is not listed, under its own name', () => {
    const groups = groupByVendor({ models: [{ id: 'gateway/echo', vendor: 'echo' }], providers: [] });
    expect(groups).toEqual([{ vendor: 'echo', label: 'echo', models: [{ id: 'gateway/echo', vendor: 'echo' }] }]);
  });
});

describe('ModelPicker', () => {
  it('offers each model under its vendor, marking local ones', () => {
    const { select } = picker();
    const groups = within(select).getAllByRole('group');
    expect(groups.map((g) => g.getAttribute('label'))).toEqual(['DeepSeek', 'Z.ai (GLM)', 'Local']);
    expect(within(groups[2]).getByRole('option').textContent).toBe('local/qwen3:8b (local)');
    expect(select.value).toBe('gateway/deepseek-flash');
  });

  it('says which model was picked', () => {
    const { select, onChange } = picker();
    fireEvent.change(select, { target: { value: 'gateway/glm-5.2' } });
    expect(onChange).toHaveBeenCalledWith('gateway/glm-5.2');
  });

  it('keeps the session’s own model listed when it is not offered now', () => {
    const { select } = picker({ value: 'echo', current: 'echo' });
    expect(select.value).toBe('echo');
    expect(within(select).getAllByRole('option')[0].textContent).toBe('echo (this session’s)');
  });

  it('points to Settings while a vendor is not ready', () => {
    picker();
    const link = screen.getByRole('link', { name: 'More models in Settings' });
    expect(link.getAttribute('href')).toBe(SETTINGS_HREF);
    expect(link.getAttribute('title')).toBe('Not ready: Anthropic');
  });

  it('shows nothing before the choices are known, or when nothing can be picked', () => {
    const { container } = render(<ModelPicker id="m" value="" current={null} choices={undefined} onChange={() => {}} />);
    expect(container.innerHTML).toBe('');
    render(<ModelPicker id="m2" value="" current={null} choices={{ models: [], providers: [] }} onChange={() => {}} />);
    expect(screen.queryByLabelText('Model')).toBeNull();
  });
});
