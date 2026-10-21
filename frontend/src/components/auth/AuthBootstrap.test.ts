import { beforeEach, describe, expect, it, vi } from 'vitest';

const refreshToken = vi.fn();
const getCurrentUser = vi.fn();

vi.mock('@/lib/api/endpoints/auth', () => ({
  authAPI: { refreshToken: () => refreshToken(), getCurrentUser: () => getCurrentUser() },
}));

import { restoreOnce } from './AuthBootstrap';

describe('restoreOnce', () => {
  beforeEach(() => {
    refreshToken.mockReset().mockResolvedValue({ access_token: 'a', token_type: 'bearer', csrf_token: 'c' });
    getCurrentUser.mockReset().mockResolvedValue({ id: 'u', email: 'demo@example.com', name: 'Demo', role: 'member' });
  });

  it('refreshes once when the restore effect runs twice at the same time', async () => {
    const [first, second] = await Promise.all([restoreOnce(), restoreOnce()]);
    expect(refreshToken).toHaveBeenCalledTimes(1);
    expect(second).toEqual(first);
  });

  it('refreshes again for a later restore', async () => {
    await restoreOnce();
    await restoreOnce();
    expect(refreshToken).toHaveBeenCalledTimes(2);
  });
});
