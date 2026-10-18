import axios from 'axios';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useAuthStore } from '../store/authStore';
import { APIClient } from './client';

type TestableClient = {
  refreshToken: () => Promise<string>;
};

describe('APIClient refresh coordination', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useAuthStore.setState({
      user: null,
      accessToken: null,
      csrfToken: null,
      isAuthenticated: false,
    });
  });

  it('uses one refresh request for concurrent callers', async () => {
    let resolveRefresh: ((value: { data: { access_token: string } }) => void) | undefined;
    const response = new Promise<{ data: { access_token: string } }>((resolve) => {
      resolveRefresh = resolve;
    });
    const post = vi.spyOn(axios, 'post').mockReturnValue(response);
    useAuthStore.setState({ csrfToken: 'csrf-from-login' });
    const client = new APIClient() as unknown as TestableClient;

    const first = client.refreshToken();
    const second = client.refreshToken();
    resolveRefresh?.({ data: { access_token: 'new-access-token', csrf_token: 'next-csrf' } as { access_token: string; csrf_token?: string } });

    await expect(Promise.all([first, second])).resolves.toEqual([
      'new-access-token',
      'new-access-token',
    ]);
    expect(post).toHaveBeenCalledTimes(1);
    expect(post.mock.calls[0]?.[2]?.headers?.['X-CSRF-Token']).toBe('csrf-from-login');
    expect(useAuthStore.getState().accessToken).toBe('new-access-token');
    expect(useAuthStore.getState().csrfToken).toBe('next-csrf');
  });

  it('rejects when refresh fails instead of retrying without a token', async () => {
    vi.spyOn(axios, 'post').mockRejectedValue(new Error('refresh rejected'));
    const client = new APIClient() as unknown as TestableClient;

    await expect(client.refreshToken()).rejects.toThrow('refresh rejected');
  });
});
