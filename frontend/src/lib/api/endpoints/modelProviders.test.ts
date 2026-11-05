/**
 * modelProvidersAPI: what it sends the BFF, and that a malformed answer is refused.
 */

import { beforeEach, describe, expect, it, Mock, vi } from 'vitest';

import { apiClient } from '../client';
import { modelProvidersAPI } from './modelProviders';

vi.mock('../client', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

const VENDOR = {
  id: 'deepseek',
  label: 'DeepSeek',
  base_url: 'https://api.deepseek.com',
  key_required: true,
  key_checked: true,
  configured: true,
  source: 'set',
  updated_at: 1,
  updated_by: 'u1',
  ready: true,
};

describe('modelProvidersAPI', () => {
  beforeEach(() => vi.clearAllMocks());

  it('lists the vendors', async () => {
    (apiClient.get as Mock).mockResolvedValue({ editable: true, vendors: [VENDOR] });
    expect((await modelProvidersAPI.list()).vendors[0].source).toBe('set');
    expect(apiClient.get).toHaveBeenCalledWith('/model-providers');
  });

  it('sends a key to its vendor’s path, and removes one', async () => {
    (apiClient.put as Mock).mockResolvedValue({ ...VENDOR, checked: true });
    (apiClient.delete as Mock).mockResolvedValue({ ...VENDOR, source: 'removed', configured: false, ready: false });
    expect((await modelProvidersAPI.setKey('deepseek', 'sk-d')).checked).toBe(true);
    expect(apiClient.put).toHaveBeenCalledWith('/model-providers/deepseek/key', { key: 'sk-d' });
    expect((await modelProvidersAPI.removeKey('a/b')).source).toBe('removed');
    expect(apiClient.delete).toHaveBeenCalledWith('/model-providers/a%2Fb/key');
  });

  it('asks the gateway to read the local server again', async () => {
    (apiClient.post as Mock).mockResolvedValue({ ...VENDOR, id: 'local', models: ['qwen3:8b'], error: null });
    expect((await modelProvidersAPI.refreshLocal()).models).toEqual(['qwen3:8b']);
    expect(apiClient.post).toHaveBeenCalledWith('/model-providers/local/refresh');
  });

  it('refuses an answer that is not a vendor list', async () => {
    (apiClient.get as Mock).mockResolvedValue({ vendors: [{ id: 'x' }] });
    await expect(modelProvidersAPI.list()).rejects.toThrow();
  });
});
