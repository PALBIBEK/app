/**
 * operateAPI: the paths, params and bodies it sends the BFF.
 */

import { describe, it, expect, beforeEach, vi, Mock } from 'vitest';
import { apiClient } from '../client';
import { operateAPI } from './operate';

vi.mock('../client', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

describe('operateAPI', () => {
  beforeEach(() => vi.clearAllMocks());

  it('asks for one session’s sandboxes by this product’s id', async () => {
    (apiClient.get as Mock).mockResolvedValue([]);
    await operateAPI.sandboxes({ sessionId: 's-1' });
    expect(apiClient.get).toHaveBeenCalledWith('/sandboxes', { params: { session_id: 's-1' } });
    await operateAPI.sandboxes();
    expect(apiClient.get).toHaveBeenLastCalledWith('/sandboxes', { params: {} });
  });

  it('defaults usage to by=day and passes a window', async () => {
    (apiClient.get as Mock).mockResolvedValue({});
    await operateAPI.usage();
    expect(apiClient.get).toHaveBeenCalledWith('/usage', { params: { by: 'day' } });
    await operateAPI.usage({ by: 'agent', sinceMs: 0, untilMs: 10 });
    expect(apiClient.get).toHaveBeenLastCalledWith('/usage', { params: { by: 'agent', since_ms: 0, until_ms: 10 } });
  });

  it('keeps slashes in secret names but escapes the rest', async () => {
    (apiClient.put as Mock).mockResolvedValue(undefined);
    await operateAPI.putSecret('github/my token', 'v');
    expect(apiClient.put).toHaveBeenCalledWith('/secrets/github/my%20token', { value: 'v' });
  });

  it('reads secret names out of the reply', async () => {
    (apiClient.get as Mock).mockResolvedValue({ names: ['A', 'B'] });
    expect(await operateAPI.secretNames()).toEqual(['A', 'B']);
  });

  it('posts a draft to the publish check', async () => {
    (apiClient.post as Mock).mockResolvedValue({ ok: true, digest: 'd', checks: [] });
    await operateAPI.checkArtifact('tool_set', { name: 't' });
    expect(apiClient.post).toHaveBeenCalledWith('/catalog/tool_set/check', { name: 't' });
  });
});
