/**
 * memory.test.ts — memoryAPI calls the backend's notebook routes, by harness.
 */

import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';

import { apiClient } from '../client';
import { memoryAPI } from './memory';

vi.mock('../client', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), patch: vi.fn() },
}));

describe('memoryAPI', () => {
  beforeEach(() => vi.clearAllMocks());

  it('lists a harness’s notebooks', async () => {
    (apiClient.get as Mock).mockResolvedValue([]);
    await memoryAPI.listNotebooks('h1');
    expect(apiClient.get).toHaveBeenCalledWith('/memory/notebooks', {
      params: { harness_id: 'h1', archived: false },
    });
  });

  it('forgets facts with a POST, so the ids are never dropped from a DELETE body', async () => {
    (apiClient.post as Mock).mockResolvedValue(undefined);
    await memoryAPI.forgetFacts('nb/1', ['f1']);
    expect(apiClient.post).toHaveBeenCalledWith('/memory/notebooks/nb%2F1/forget', { ids: ['f1'] });
  });

  it('restores a notebook to a revision', async () => {
    (apiClient.post as Mock).mockResolvedValue(undefined);
    await memoryAPI.restore('nb1', 3);
    expect(apiClient.post).toHaveBeenCalledWith('/memory/notebooks/nb1/restore', { revision: 3 });
  });
});
