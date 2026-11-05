import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { operateAPI } from '@/lib/api/endpoints/operate';
import type { ModelProvider } from '@/lib/api/types/provider.types';
import { useAuthStore } from '@/lib/store/authStore';

import { ModelProviders, providerState } from './ModelProviders';

const vendor = (over: Partial<ModelProvider>): ModelProvider => ({
  id: 'anthropic',
  label: 'Anthropic',
  base_url: 'https://api.anthropic.com',
  key_required: true,
  key_checked: true,
  configured: false,
  source: 'absent',
  ready: false,
  address_settable: false,
  ...over,
});

const LOCAL = vendor({ id: 'local', label: 'Local', base_url: '', key_required: false, address_settable: true, address_source: 'absent', models: [], unreachable: null });
const VENDORS = [vendor({}), vendor({ id: 'openai', label: 'OpenAI', source: 'environment', configured: true, ready: true }), LOCAL];

function as(role: 'owner' | 'admin' | 'member'): void {
  useAuthStore.setState({ user: { id: 'u', email: 'u@example.com', name: 'U', role }, isAuthenticated: true, accessToken: 't' });
}

function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ModelProviders />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.spyOn(operateAPI, 'modelProviders').mockResolvedValue({ editable: true, vendors: VENDORS });
  vi.spyOn(window, 'confirm').mockReturnValue(true);
});
afterEach(() => vi.restoreAllMocks());

describe('model providers', () => {
  it('say what each provider has, in words', () => {
    expect(providerState(vendor({})).text).toBe('No key');
    expect(providerState(vendor({ source: 'set', ready: true }))).toEqual({ ready: true, text: 'Key added here' });
    expect(providerState(vendor({ source: 'removed' })).text).toBe('Key removed: its models are off');
    expect(providerState(LOCAL).text).toBe('Not set up: add your server’s address');
    expect(providerState({ ...LOCAL, base_url: 'http://box/v1', unreachable: 'the server could not be reached' }).text).toBe('Not answering: the server could not be reached');
    expect(providerState({ ...LOCAL, base_url: 'http://box/v1', models: ['a', 'b'], ready: true })).toEqual({ ready: true, text: '2 models found' });
  });

  it('are for owners and admins: a member sees nothing and nothing is asked', () => {
    as('member');
    const { container } = show();
    expect(container.innerHTML).toBe('');
    expect(operateAPI.modelProviders).not.toHaveBeenCalled();
  });

  it('take a key, checked by the provider, and never show it again', async () => {
    as('admin');
    const set = vi.spyOn(operateAPI, 'setProviderKey').mockResolvedValue(vendor({ source: 'set', configured: true, ready: true }));
    show();
    const anthropic = await screen.findByTestId('provider-anthropic');
    expect(within(anthropic).getByTestId('provider-state').textContent).toBe('No key');
    expect(within(screen.getByTestId('provider-openai')).getByTestId('provider-state').textContent).toBe('Key set by the deployment');

    const input = within(anthropic).getByLabelText('Anthropic API key') as HTMLInputElement;
    fireEvent.change(input, { target: { value: ' sk-ant-123 ' } });
    await act(async () => fireEvent.click(within(anthropic).getByRole('button', { name: 'Add key' })));

    expect(set).toHaveBeenCalledWith('anthropic', 'sk-ant-123');
    expect(input.type).toBe('password');
    await waitFor(() => expect(input.value).toBe(''));
  });

  it('remove a key when asked', async () => {
    as('owner');
    const remove = vi.spyOn(operateAPI, 'removeProviderKey').mockResolvedValue(vendor({ id: 'openai', source: 'removed' }));
    show();
    const openai = await screen.findByTestId('provider-openai');
    await act(async () => fireEvent.click(within(openai).getByRole('button', { name: 'Remove the OpenAI key' })));
    expect(remove).toHaveBeenCalledWith('openai');
  });

  it('turn a local server on by its address', async () => {
    as('admin');
    const set = vi.spyOn(operateAPI, 'setProviderAddress').mockResolvedValue({ ...LOCAL, base_url: 'http://gpu-box:11434/v1', models: ['qwen3:8b'], ready: true });
    show();
    const local = await screen.findByTestId('provider-local');
    expect(within(local).queryByLabelText('Local API key')).toBeNull(); // no key before there is a server

    fireEvent.change(within(local).getByLabelText('Local server address'), { target: { value: 'http://gpu-box:11434/v1' } });
    await act(async () => fireEvent.click(within(local).getByRole('button', { name: 'Save and connect' })));

    expect(set).toHaveBeenCalledWith('local', 'http://gpu-box:11434/v1');
  });

  it('check a local server again that was not answering', async () => {
    as('admin');
    const down = { ...LOCAL, base_url: 'http://gpu-box:11434/v1', unreachable: 'the server could not be reached' };
    vi.mocked(operateAPI.modelProviders).mockResolvedValue({ editable: true, vendors: [down] });
    const set = vi.spyOn(operateAPI, 'setProviderAddress').mockResolvedValue({ ...down, unreachable: null, models: ['qwen3:8b'], ready: true });
    show();
    const local = await screen.findByTestId('provider-local');
    expect(within(local).getByTestId('provider-state').textContent).toBe('Not answering: the server could not be reached');

    await act(async () => fireEvent.click(within(local).getByRole('button', { name: 'Check again' })));

    expect(set).toHaveBeenCalledWith('local', 'http://gpu-box:11434/v1');
  });

  it('say when keys are the deployment’s, not set here', async () => {
    as('admin');
    vi.mocked(operateAPI.modelProviders).mockResolvedValue({ editable: false, vendors: VENDORS });
    show();
    const anthropic = await screen.findByTestId('provider-anthropic');
    expect(within(anthropic).getByText('Keys are set in the deployment’s environment here.')).toBeTruthy();
    expect(within(anthropic).queryByRole('textbox')).toBeNull();
  });
});
