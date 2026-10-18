'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';

import { toolsAPI } from '@/lib/api/endpoints/tools';
import type { ToolCreate, ToolListParams, ToolUpdate } from '@/lib/api/types/tool.types';
import { useAuthStore } from '@/lib/store/authStore';

function sanitizeErrorMessage(error: Error): string {
  const message = error.message || 'An error occurred';
  if (message.includes('at ') || message.includes('/') || message.includes('\\')) {
    return 'An error occurred. Please try again.';
  }
  return message.length > 100 ? `${message.substring(0, 100)}...` : message;
}

export const toolKeys = {
  all: ['tools'] as const,
  lists: () => [...toolKeys.all, 'list'] as const,
  list: (filters: string) => [...toolKeys.lists(), { filters }] as const,
  details: () => [...toolKeys.all, 'detail'] as const,
  detail: (id: string) => [...toolKeys.details(), id] as const,
};

export function useTools(params?: ToolListParams) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  return useQuery({
    queryKey: toolKeys.list(JSON.stringify(params ?? {})),
    queryFn: () => toolsAPI.list(params),
    enabled: isAuthenticated,
  });
}

export function useTool(id: string | undefined) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  return useQuery({
    queryKey: toolKeys.detail(id ?? ''),
    queryFn: () => toolsAPI.getById(id!),
    enabled: isAuthenticated && Boolean(id),
  });
}

export function useCreateTool() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: ToolCreate) => toolsAPI.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: toolKeys.lists() });
      toast.success('Tool created successfully');
    },
    onError: (error: Error) => {
      toast.error(sanitizeErrorMessage(error) || 'Failed to create tool');
    },
  });
}

export function useUpdateTool() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: ToolUpdate }) => toolsAPI.update(id, data),
    onSuccess: (_tool, vars) => {
      queryClient.invalidateQueries({ queryKey: toolKeys.lists() });
      queryClient.invalidateQueries({ queryKey: toolKeys.detail(vars.id) });
      toast.success('Tool updated successfully');
    },
    onError: (error: Error) => {
      toast.error(sanitizeErrorMessage(error) || 'Failed to update tool');
    },
  });
}

export function useDeleteTool() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: string) => toolsAPI.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: toolKeys.lists() });
      toast.success('Tool deleted successfully');
    },
    onError: (error: Error) => {
      toast.error(sanitizeErrorMessage(error) || 'Failed to delete tool');
    },
  });
}
