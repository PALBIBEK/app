'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';

import { memoryAPI } from '@/lib/api/endpoints/memory';
import type {
  MemoryCreateRequest,
  MemoryListParams,
  MemorySearchRequest,
  MemoryUpdateRequest,
} from '@/lib/api/types/memory.types';
import { useAuthStore } from '@/lib/store/authStore';

function sanitizeErrorMessage(error: Error): string {
  const message = error.message || 'An error occurred';
  if (message.includes('at ') || message.includes('/') || message.includes('\\')) {
    return 'An error occurred. Please try again.';
  }
  return message.length > 100 ? `${message.substring(0, 100)}...` : message;
}

export const memoryKeys = {
  all: ['memory'] as const,
  lists: () => [...memoryKeys.all, 'list'] as const,
  list: (filters: string) => [...memoryKeys.lists(), { filters }] as const,
  details: () => [...memoryKeys.all, 'detail'] as const,
  detail: (id: string) => [...memoryKeys.details(), id] as const,
};

export function useMemory(params?: MemoryListParams) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  return useQuery({
    queryKey: memoryKeys.list(JSON.stringify(params ?? {})),
    queryFn: () => memoryAPI.list(params),
    enabled: isAuthenticated,
  });
}

export function useMemoryEntry(id: string | undefined) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  return useQuery({
    queryKey: memoryKeys.detail(id ?? ''),
    queryFn: () => memoryAPI.getById(id!),
    enabled: isAuthenticated && Boolean(id),
  });
}

/** Alias used by older stubs */
export const useMemoryItem = useMemoryEntry;

export function useCreateMemory() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: MemoryCreateRequest) => memoryAPI.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: memoryKeys.lists() });
      toast.success('Memory saved');
    },
    onError: (error: Error) => {
      toast.error(sanitizeErrorMessage(error) || 'Failed to create memory');
    },
  });
}

export function useUpdateMemory() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: MemoryUpdateRequest }) =>
      memoryAPI.update(id, data),
    onSuccess: (_entry, vars) => {
      queryClient.invalidateQueries({ queryKey: memoryKeys.lists() });
      queryClient.invalidateQueries({ queryKey: memoryKeys.detail(vars.id) });
      toast.success('Memory updated');
    },
    onError: (error: Error) => {
      toast.error(sanitizeErrorMessage(error) || 'Failed to update memory');
    },
  });
}

export function useDeleteMemory() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: string) => memoryAPI.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: memoryKeys.lists() });
      toast.success('Memory deleted');
    },
    onError: (error: Error) => {
      toast.error(sanitizeErrorMessage(error) || 'Failed to delete memory');
    },
  });
}

export function useSearchMemory() {
  return useMutation({
    mutationFn: (body: MemorySearchRequest) => memoryAPI.search(body),
    onError: (error: Error) => {
      toast.error(sanitizeErrorMessage(error) || 'Search failed');
    },
  });
}
