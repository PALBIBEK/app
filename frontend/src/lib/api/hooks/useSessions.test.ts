/**
 * useSessions.test.ts — React Query hooks for D4 sessions API.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { sessionsAPI } from '../endpoints/sessions';
import {
  sessionKeys,
  useSessions,
  useSession,
  useCreateSession,
  useSendMessage,
  useAbortSession,
  useDeleteSession,
} from './useSessions';
import type { Session, SessionListResponse, SessionMessageResponse } from '../types/session.types';

vi.mock('../endpoints/sessions', () => ({
  sessionsAPI: {
    list: vi.fn(),
    getById: vi.fn(),
    create: vi.fn(),
    sendMessage: vi.fn(),
    abort: vi.fn(),
    delete: vi.fn(),
  },
}));

vi.mock('sonner', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

const createMockSession = (overrides: Partial<Session> = {}): Session => ({
  id: 'session-123',
  harness_id: 'harness-456',
  status: 'active',
  harness_version: '1.0.0',
  runtime: 'echo',
  composition_id: null,
  composition_digest: null,
  title: 'Test',
  message_count: 5,
  total_cost: 0.05,
  last_seq: 3,
  is_busy: false,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:05:00Z',
  pin: {
    harness_version: '1.0.0',
    runtime: 'echo',
    composition_id: null,
    composition_digest: null,
  },
  ...overrides,
});

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return React.createElement(QueryClientProvider, { client: queryClient }, children);
  };
};

describe('sessionKeys', () => {
  it('should generate correct query keys', () => {
    expect(sessionKeys.all).toEqual(['sessions']);
    expect(sessionKeys.lists()).toEqual(['sessions', 'list']);
    expect(sessionKeys.list('{"status":"active"}')).toEqual([
      'sessions',
      'list',
      { filters: '{"status":"active"}' },
    ]);
    expect(sessionKeys.details()).toEqual(['sessions', 'detail']);
    expect(sessionKeys.detail('session-123')).toEqual(['sessions', 'detail', 'session-123']);
  });
});

describe('useSessions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('should fetch sessions list', async () => {
    const mockData: SessionListResponse = {
      data: [createMockSession()],
      total: 1,
      page: 1,
      page_size: 20,
    };
    vi.mocked(sessionsAPI.list).mockResolvedValue(mockData);

    const { result } = renderHook(() => useSessions(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.data).toHaveLength(1);
    expect(sessionsAPI.list).toHaveBeenCalledWith(undefined);
  });

  it('should pass filter parameters', async () => {
    vi.mocked(sessionsAPI.list).mockResolvedValue({
      data: [],
      total: 0,
      page: 1,
      page_size: 20,
    });
    const params = { harness_id: 'harness-123', page: 1 };

    const { result } = renderHook(() => useSessions(params), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(sessionsAPI.list).toHaveBeenCalledWith(params);
  });
});

describe('useSession', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('should fetch session by ID', async () => {
    vi.mocked(sessionsAPI.getById).mockResolvedValue(createMockSession());

    const { result } = renderHook(() => useSession('session-123'), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.id).toBe('session-123');
  });

  it('should not fetch when ID is empty', () => {
    const { result } = renderHook(() => useSession(''), {
      wrapper: createWrapper(),
    });

    expect(result.current.isFetching).toBe(false);
    expect(sessionsAPI.getById).not.toHaveBeenCalled();
  });
});

describe('useCreateSession', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('should create a new session', async () => {
    vi.mocked(sessionsAPI.create).mockResolvedValue(createMockSession());

    const { result } = renderHook(() => useCreateSession(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ harness_id: 'harness-123' });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(sessionsAPI.create).toHaveBeenCalledWith({ harness_id: 'harness-123' });
  });
});

describe('useSendMessage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('should send message to session', async () => {
    const mockResponse: SessionMessageResponse = {
      turn_id: 'turn-1',
      command_id: 'cmd-1',
      status: 'accepted',
    };
    vi.mocked(sessionsAPI.sendMessage).mockResolvedValue(mockResponse);

    const { result } = renderHook(() => useSendMessage(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ sessionId: 'session-123', content: 'Hello' });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(sessionsAPI.sendMessage).toHaveBeenCalledWith('session-123', 'Hello');
  });
});

describe('useAbortSession', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('should abort session', async () => {
    vi.mocked(sessionsAPI.abort).mockResolvedValue(undefined);

    const { result } = renderHook(() => useAbortSession(), {
      wrapper: createWrapper(),
    });

    result.current.mutate('session-123');

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(sessionsAPI.abort).toHaveBeenCalledWith('session-123');
  });
});

describe('useDeleteSession', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('should delete session', async () => {
    vi.mocked(sessionsAPI.delete).mockResolvedValue(undefined);

    const { result } = renderHook(() => useDeleteSession(), {
      wrapper: createWrapper(),
    });

    result.current.mutate('session-123');

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(sessionsAPI.delete).toHaveBeenCalledWith('session-123');
  });
});
