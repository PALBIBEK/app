/**
 * useHarnesses.test.ts
 *
 * RED Phase: Write failing tests first
 * Reference: 08-testing-patterns.md (AAA Pattern)
 * Reference: FRONTEND_BUILD_CONFIG.md (TanStack Query hooks)
 *
 * Test Strategy:
 * 1. Test query key factory
 * 2. Test useHarnesses hook returns data
 * 3. Test useHarnesses with search filter
 * 4. Test useHarness hook with ID
 * 5. Test useCreateHarness mutation
 * 6. Test useUpdateHarness mutation
 * 7. Test useDeleteHarness mutation
 * 8. Test cache invalidation on mutations
 * 9. Test toast notifications on success/error
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { harnessesAPI } from '../endpoints/harnesses';
import {
  harnessKeys,
  useHarnesses,
  useHarness,
  useCreateHarness,
  useUpdateHarness,
  useDeleteHarness,
} from './useHarnesses';
import type { Harness, HarnessListResponse } from '../types/harness.types';
import { useAuthStore } from '../../store/authStore';

// Mock the harnesses API
vi.mock('../endpoints/harnesses', () => ({
  harnessesAPI: {
    list: vi.fn(),
    getById: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
  },
}));

// Mock sonner toast
vi.mock('sonner', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

// Test data factory
const createMockHarness = (overrides: Partial<Harness> = {}): Harness => ({
  id: 'harness-123',
  name: 'coding-assistant-v1',
  description: 'A coding assistant harness for Python development',
  model: 'claude-sonnet-4',
  mode: 'agent',
  tools: ['code-analyzer', 'file-writer'],
  memory: ['project-context'],
  system_prompt: 'You are a helpful coding assistant.',
  temperature: 0.7,
  max_tokens: 4096,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
  ...overrides,
});

// Wrapper for React Query
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

describe('harnessKeys', () => {
  it('should generate correct query keys', () => {
    expect(harnessKeys.all).toEqual(['harnesses']);
    expect(harnessKeys.lists()).toEqual(['harnesses', 'list']);
    expect(harnessKeys.list('{\"search\":\"coding\"}')).toEqual([
      'harnesses',
      'list',
      { filters: '{\"search\":\"coding\"}' },
    ]);
    expect(harnessKeys.details()).toEqual(['harnesses', 'detail']);
    expect(harnessKeys.detail('harness-123')).toEqual(['harnesses', 'detail', 'harness-123']);
  });
});

describe('useHarnesses', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAuthStore.setState({
      isAuthenticated: true,
      accessToken: 'test-access',
      csrfToken: 'test-csrf',
      sessionReady: true,
    });
  });

  it('should fetch harnesses list', async () => {
    // Arrange
    const mockData: HarnessListResponse = {
      data: [createMockHarness()],
      total: 1,
      page: 1,
      page_size: 20,
    };
    vi.mocked(harnessesAPI.list).mockResolvedValue(mockData);

    // Act
    const { result } = renderHook(() => useHarnesses(), {
      wrapper: createWrapper(),
    });

    // Assert
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(mockData);
    expect(harnessesAPI.list).toHaveBeenCalledWith(undefined);
  });

  it('should fetch harnesses with search parameter', async () => {
    // Arrange
    const mockData: HarnessListResponse = {
      data: [createMockHarness()],
      total: 1,
      page: 1,
      page_size: 20,
    };
    vi.mocked(harnessesAPI.list).mockResolvedValue(mockData);

    // Act
    const { result } = renderHook(() => useHarnesses({ search: 'coding' }), {
      wrapper: createWrapper(),
    });

    // Assert
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(harnessesAPI.list).toHaveBeenCalledWith({ search: 'coding' });
  });

  it('should handle query errors', async () => {
    // Arrange
    vi.mocked(harnessesAPI.list).mockRejectedValue(new Error('Network error'));

    // Act
    const { result } = renderHook(() => useHarnesses(), {
      wrapper: createWrapper(),
    });

    // Assert
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(Error);
  });
});

describe('useHarness', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAuthStore.setState({
      isAuthenticated: true,
      accessToken: 'test-access',
      csrfToken: 'test-csrf',
      sessionReady: true,
    });
  });

  it('should fetch harness by ID', async () => {
    // Arrange
    const mockHarness = createMockHarness();
    vi.mocked(harnessesAPI.getById).mockResolvedValue(mockHarness);

    // Act
    const { result } = renderHook(() => useHarness('harness-123'), {
      wrapper: createWrapper(),
    });

    // Assert
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(mockHarness);
    expect(harnessesAPI.getById).toHaveBeenCalledWith('harness-123');
  });

  it('should not fetch when ID is empty', () => {
    // Arrange & Act
    const { result } = renderHook(() => useHarness(''), {
      wrapper: createWrapper(),
    });

    // Assert
    expect(result.current.fetchStatus).toBe('idle');
    expect(harnessesAPI.getById).not.toHaveBeenCalled();
  });

  it('should not fetch when unauthenticated', () => {
    useAuthStore.setState({ isAuthenticated: false });
    const { result } = renderHook(() => useHarness('harness-123'), {
      wrapper: createWrapper(),
    });
    expect(result.current.fetchStatus).toBe('idle');
    expect(harnessesAPI.getById).not.toHaveBeenCalled();
  });
});

describe('useCreateHarness', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('should create harness and show success toast', async () => {
    // Arrange
    const newHarness = {
      name: 'new-harness',
      description: 'A new harness',
      model: 'claude-sonnet-4' as const,
      mode: 'chat' as const,
      tools: [],
      memory: [],
    };
    const mockResponse = createMockHarness(newHarness);
    vi.mocked(harnessesAPI.create).mockResolvedValue(mockResponse);

    const toast = await import('sonner');

    // Act
    const { result } = renderHook(() => useCreateHarness(), {
      wrapper: createWrapper(),
    });

    result.current.mutate(newHarness);

    // Assert
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(harnessesAPI.create).toHaveBeenCalledWith(newHarness);
    expect(toast.toast.success).toHaveBeenCalledWith('Harness created successfully');
  });

  it('should handle create errors and show error toast', async () => {
    // Arrange
    const newHarness = {
      name: 'new-harness',
      model: 'claude-sonnet-4' as const,
      mode: 'chat' as const,
      tools: [],
      memory: [],
    };
    vi.mocked(harnessesAPI.create).mockRejectedValue(new Error('Creation failed'));

    const toast = await import('sonner');

    // Act
    const { result } = renderHook(() => useCreateHarness(), {
      wrapper: createWrapper(),
    });

    result.current.mutate(newHarness);

    // Assert
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(toast.toast.error).toHaveBeenCalledWith('Creation failed');
  });
});

describe('useUpdateHarness', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('should update harness and show success toast', async () => {
    // Arrange
    const updateData = {
      name: 'updated-harness',
      description: 'Updated description',
    };
    const mockResponse = createMockHarness(updateData);
    vi.mocked(harnessesAPI.update).mockResolvedValue(mockResponse);

    const toast = await import('sonner');

    // Act
    const { result } = renderHook(() => useUpdateHarness(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ id: 'harness-123', data: updateData });

    // Assert
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(harnessesAPI.update).toHaveBeenCalledWith('harness-123', updateData);
    expect(toast.toast.success).toHaveBeenCalledWith('Harness updated successfully');
  });

  it('should handle update errors and show error toast', async () => {
    // Arrange
    const updateData = { name: 'updated-harness' };
    vi.mocked(harnessesAPI.update).mockRejectedValue(new Error('Update failed'));

    const toast = await import('sonner');

    // Act
    const { result } = renderHook(() => useUpdateHarness(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ id: 'harness-123', data: updateData });

    // Assert
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(toast.toast.error).toHaveBeenCalledWith('Update failed');
  });
});

describe('useDeleteHarness', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('should delete harness and show success toast', async () => {
    // Arrange
    vi.mocked(harnessesAPI.delete).mockResolvedValue(undefined);

    const toast = await import('sonner');

    // Act
    const { result } = renderHook(() => useDeleteHarness(), {
      wrapper: createWrapper(),
    });

    result.current.mutate('harness-123');

    // Assert
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(harnessesAPI.delete).toHaveBeenCalledWith('harness-123');
    expect(toast.toast.success).toHaveBeenCalledWith('Harness deleted successfully');
  });

  it('should handle delete errors and show error toast', async () => {
    // Arrange
    vi.mocked(harnessesAPI.delete).mockRejectedValue(new Error('Delete failed'));

    const toast = await import('sonner');

    // Act
    const { result } = renderHook(() => useDeleteHarness(), {
      wrapper: createWrapper(),
    });

    result.current.mutate('harness-123');

    // Assert
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(toast.toast.error).toHaveBeenCalledWith('Delete failed');
  });
});
