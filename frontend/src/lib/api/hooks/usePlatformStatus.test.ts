/**
 * Platform status hook / client tests (D2).
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';

import { platformAPI } from '../endpoints/platform';
import { usePlatformStatus } from './usePlatformStatus';
import { useAuthStore } from '../../store/authStore';

vi.mock('../endpoints/platform', () => ({
  platformAPI: {
    getStatus: vi.fn(),
  },
}));

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return React.createElement(QueryClientProvider, { client: queryClient }, children);
  };
};

describe('usePlatformStatus', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAuthStore.setState({
      isAuthenticated: true,
      accessToken: 'test',
      csrfToken: 'csrf',
      sessionReady: true,
    });
  });

  it('test_usePlatformStatus_fetches_system_platform_status_only', async () => {
    vi.mocked(platformAPI.getStatus).mockResolvedValue({
      ready: false,
      run_allowed: false,
      checked_at: new Date().toISOString(),
      components: {
        supervisor: { ready: false, status: 'not_ready', reason: 'draining' },
      },
    });

    const { result } = renderHook(() => usePlatformStatus(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(platformAPI.getStatus).toHaveBeenCalledTimes(1);
    expect(result.current.data?.run_allowed).toBe(false);
  });

  it('does not fetch when unauthenticated', () => {
    useAuthStore.setState({ isAuthenticated: false });
    const { result } = renderHook(() => usePlatformStatus(), {
      wrapper: createWrapper(),
    });
    expect(result.current.fetchStatus).toBe('idle');
    expect(platformAPI.getStatus).not.toHaveBeenCalled();
  });
});
