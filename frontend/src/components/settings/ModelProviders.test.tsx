/**
 * Model providers in Settings: each vendor's status in words, a key typed once and sent to the
 * gateway (never shown again), removal, and who may see any of it.
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ModelProvider, ModelProviders as Listing } from '@/lib/api/types/modelProvider.types';

const state = vi.hoisted(() => ({
  role: 'admin' as string,
  listing: undefined as Listing | undefined,
  setKey: vi.fn(),
  removeKey: vi.fn(),
}));

vi.mock('@/lib/store/authStore', () => ({
  useAuthStore: (pick: (s: { user: { role: string } }) => unknown) => pick({ user: { role: state.role } }),
}));
vi.mock('@/lib/api/hooks/useModelProviders', async (original) => ({
  canManageModelProviders: (await original<typeof import('@/lib/api/hooks/useModelProviders')>()).canManageModelProviders,
  useModelProviders: () => ({ data: state.listing, isLoading: false, isError: false, error: null }),
  useSetModelProviderKey: () => ({ mutateAsync: state.setKey, isPending: false }),
  useRemoveModelProviderKey: () => ({ mutateAsync: state.removeKey, isPending: false }),
}));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { toast } from 'sonner';

import { ModelProviders, vendorStatus } from './ModelProviders';

function vendor(over: Partial<ModelProvider> = {}): ModelProvider {
  return {
    id: 'deepseek',
    label: 'DeepSeek',
    base_url: 'https://api.deepseek.com',
    key_required: true,
    key_checked: true,
    configured: false,
    source: 'absent',
    updated_at: null,
    updated_by: null,
    ready: false,
    ...over,
  };
}

beforeEach(() => {
  state.role = 'admin';
  state.listing = {
    editable: true,
    vendors: [
      vendor(),
      vendor({ id: 'openai', label: 'OpenAI', source: 'environment', configured: true, ready: true }),
      vendor({ id: 'zai', label: 'Z.ai (GLM)', key_checked: false }),
      vendor({ id: 'local', label: 'Local', base_url: '', key_required: false, key_checked: true }),
    ],
  };
  state.setKey.mockReset();
  state.removeKey.mockReset();
});
afterEach(cleanup);

describe('vendorStatus', () => {
  it.each([
    [vendor(), 'No key', false],
    [vendor({ source: 'set', configured: true, ready: true }), 'Key set here', true],
    [vendor({ source: 'environment', configured: true, ready: true }), "Key from the server's environment", true],
    [vendor({ source: 'removed' }), 'Removed here', false],
    [vendor({ key_required: false, base_url: '' }), 'Not set up', false],
    [vendor({ key_required: false, base_url: 'http://localhost:11434/v1', ready: true }), 'Ready', true],
  ])('says what it is', (v, label, ready) => {
    expect(vendorStatus(v)).toEqual({ label, ready });
  });
});

describe('ModelProviders', () => {
  it('lists every vendor with its status, never a key', () => {
    render(<ModelProviders />);
    expect(within(screen.getByTestId('vendor-openai')).getByTestId('vendor-status').textContent).toBe(
      "Key from the server's environment",
    );
    expect(within(screen.getByTestId('vendor-local')).getByTestId('vendor-status').textContent).toBe('Not set up');
    expect(within(screen.getByTestId('vendor-zai')).getByRole('button', { name: 'Save' })).toBeTruthy();
    expect(within(screen.getByTestId('vendor-deepseek')).getByRole('button', { name: 'Check and save' })).toBeTruthy();
  });

  it('sends a typed key once, clears it, and says the vendor accepted it', async () => {
    state.setKey.mockResolvedValue(vendor({ source: 'set', ready: true, checked: true }));
    render(<ModelProviders />);
    const input = screen.getByLabelText('DeepSeek API key') as HTMLInputElement;
    expect(input.type).toBe('password');
    fireEvent.change(input, { target: { value: '  sk-d  ' } });
    fireEvent.submit(screen.getByRole('form', { name: 'DeepSeek key' }));
    await waitFor(() => expect(state.setKey).toHaveBeenCalledWith({ vendor: 'deepseek', key: 'sk-d' }));
    await waitFor(() => expect(input.value).toBe(''));
    expect(toast.success).toHaveBeenCalledWith('DeepSeek accepted the key. Its models can be picked now.');
  });

  it('says when a key was kept unchecked', async () => {
    state.setKey.mockResolvedValue(vendor({ id: 'zai', label: 'Z.ai (GLM)', source: 'set', ready: true, checked: false }));
    render(<ModelProviders />);
    fireEvent.change(screen.getByLabelText('Z.ai (GLM) API key'), { target: { value: 'sk-z' } });
    fireEvent.submit(screen.getByRole('form', { name: 'Z.ai (GLM) key' }));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith(expect.stringContaining('no way to check a key first')));
  });

  it('shows the vendor’s refusal and keeps the typed key', async () => {
    state.setKey.mockRejectedValue(new Error('DeepSeek refused this key'));
    render(<ModelProviders />);
    const input = screen.getByLabelText('DeepSeek API key') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'bad' } });
    fireEvent.submit(screen.getByRole('form', { name: 'DeepSeek key' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('DeepSeek refused this key'));
    expect(input.value).toBe('bad');
  });

  it('removes a key from the environment too', async () => {
    state.removeKey.mockResolvedValue(vendor({ id: 'openai', source: 'removed' }));
    render(<ModelProviders />);
    expect(within(screen.getByTestId('vendor-deepseek')).queryByRole('button', { name: 'Remove' })).toBeNull();
    fireEvent.click(within(screen.getByTestId('vendor-openai')).getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(state.removeKey).toHaveBeenCalledWith('openai'));
  });

  it('only shows statuses where keys come from the environment', () => {
    state.listing = { ...state.listing!, editable: false };
    render(<ModelProviders />);
    expect(screen.getByTestId('keys-not-editable')).toBeTruthy();
    expect(screen.queryByRole('form')).toBeNull();
  });

  it('is for owners and admins only', () => {
    state.role = 'member';
    render(<ModelProviders />);
    expect(screen.getByText(/Only owners and admins/)).toBeTruthy();
    expect(screen.queryByTestId('vendor-deepseek')).toBeNull();
  });
});
