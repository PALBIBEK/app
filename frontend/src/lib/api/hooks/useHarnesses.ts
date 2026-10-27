/**
 * useHarnesses.ts
 *
 * GREEN Phase: React Query hooks for Harnesses API
 * Reference: FRONTEND_BUILD_CONFIG.md (TanStack Query patterns)
 * Reference: 08-testing-patterns.md
 *
 * Pattern Applied:
 * - Query key factory for cache management
 * - Cache invalidation on mutations
 * - Toast notifications for user feedback
 *
 * SECURITY NOTES (VibeSec Audit):
 * - Error messages are sanitized before display
 * - Server-side validation and authorization required
 */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { harnessesAPI } from '../endpoints/harnesses';
import { HarnessBindRequest, HarnessCreate, HarnessUpdate } from '../types/harness.types';
import { toast } from 'sonner';
import { useAuthStore } from '../../store/authStore';

/**
 * SECURITY: Sanitize error messages to prevent information disclosure
 * Removes stack traces and internal details
 */
function sanitizeErrorMessage(error: Error): string {
  const message = error.message || 'An error occurred';
  // Remove potential stack traces or internal paths
  if (message.includes('at ') || message.includes('/') || message.includes('\\')) {
    return 'An error occurred. Please try again.';
  }
  // Limit message length to prevent verbose server errors
  return message.length > 100 ? message.substring(0, 100) + '...' : message;
}

/**
 * Query key factory for harnesses
 * Enables precise cache invalidation
 */
export const harnessKeys = {
  all: ['harnesses'] as const,
  lists: () => [...harnessKeys.all, 'list'] as const,
  list: (filters: string) => [...harnessKeys.lists(), { filters }] as const,
  details: () => [...harnessKeys.all, 'detail'] as const,
  detail: (id: string) => [...harnessKeys.details(), id] as const,
  usage: (id: string) => [...harnessKeys.all, 'usage', id] as const,
};

/**
 * Fetch list of harnesses with optional filters
 */
export function useHarnesses(params?: { search?: string }) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  return useQuery({
    queryKey: harnessKeys.list(JSON.stringify(params)),
    queryFn: () => harnessesAPI.list(params),
    enabled: isAuthenticated,
  });
}

/** How many sessions run each published version of the agent. */
export function useHarnessUsage(id: string | undefined) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  return useQuery({
    queryKey: harnessKeys.usage(id ?? ''),
    queryFn: () => harnessesAPI.usage(id!),
    enabled: isAuthenticated && !!id,
    staleTime: 30_000,
  });
}

/**
 * Fetch single harness by ID
 */
export function useHarness(id: string) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  return useQuery({
    queryKey: harnessKeys.detail(id),
    queryFn: () => harnessesAPI.getById(id),
    enabled: isAuthenticated && !!id,
  });
}

/**
 * Create a new harness
 */
export function useCreateHarness() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: HarnessCreate) => harnessesAPI.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: harnessKeys.lists() });
      toast.success('Harness created successfully');
    },
    onError: (error: Error) => {
      toast.error(sanitizeErrorMessage(error) || 'Failed to create harness');
    },
  });
}

/**
 * Update an existing harness
 */
export function useUpdateHarness() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: HarnessUpdate }) =>
      harnessesAPI.update(id, data),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: harnessKeys.detail(variables.id) });
      queryClient.invalidateQueries({ queryKey: harnessKeys.lists() });
      toast.success('Harness updated successfully');
    },
    onError: (error: Error) => {
      toast.error(sanitizeErrorMessage(error) || 'Failed to update harness');
    },
  });
}

/**
 * Delete a harness
 */
export function useDeleteHarness() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: string) => harnessesAPI.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: harnessKeys.lists() });
      toast.success('Harness deleted successfully');
    },
    onError: (error: Error) => {
      toast.error(sanitizeErrorMessage(error) || 'Failed to delete harness');
    },
  });
}

/**
 * Bind harness to shipped / selected composition (D3)
 */
export function useBindHarness() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, data }: { id: string; data?: HarnessBindRequest }) =>
      harnessesAPI.bind(id, data ?? {}),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: harnessKeys.detail(variables.id) });
      queryClient.invalidateQueries({ queryKey: harnessKeys.lists() });
      toast.success('Harness bound to composition');
    },
    onError: (error: Error) => {
      toast.error(sanitizeErrorMessage(error) || 'Failed to bind harness');
    },
  });
}
