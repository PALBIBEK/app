/**
 * useSessions.ts
 *
 * Sessions React Query Hooks — product BFF (D4).
 * create / sendMessage / abort / delete. No pause/resume.
 */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { sessionsAPI } from '../endpoints/sessions';
import { SessionCreateRequest } from '../types/session.types';
import { toast } from 'sonner';

export const sessionKeys = {
  all: ['sessions'] as const,
  lists: () => [...sessionKeys.all, 'list'] as const,
  list: (filters: string) => [...sessionKeys.lists(), { filters }] as const,
  details: () => [...sessionKeys.all, 'detail'] as const,
  detail: (id: string) => [...sessionKeys.details(), id] as const,
};

/**
 * Hook to fetch sessions list with optional filters
 */
export function useSessions(params?: {
  harness_id?: string;
  page?: number;
  page_size?: number;
}) {
  return useQuery({
    queryKey: sessionKeys.list(JSON.stringify(params ?? {})),
    queryFn: () => sessionsAPI.list(params),
  });
}

/**
 * Hook to fetch a single session
 */
export function useSession(id: string) {
  return useQuery({
    queryKey: sessionKeys.detail(id),
    queryFn: () => sessionsAPI.getById(id),
    enabled: !!id,
  });
}

/**
 * Hook to create a new session (harness_id only — pins applied server-side)
 */
export function useCreateSession() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: SessionCreateRequest) => sessionsAPI.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: sessionKeys.lists() });
      toast.success('Session created successfully');
    },
    onError: (error: Error) => {
      toast.error(error.message || 'Failed to create session');
    },
  });
}

/**
 * Hook to send a message to a session
 */
export function useSendMessage() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ sessionId, content }: { sessionId: string; content: string }) =>
      sessionsAPI.sendMessage(sessionId, content),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({
        queryKey: sessionKeys.detail(variables.sessionId),
      });
    },
    onError: (error: Error) => {
      toast.error(error.message || 'Failed to send message');
    },
  });
}

/**
 * Hook to abort in-flight turn work (not terminate)
 */
export function useAbortSession() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (sessionId: string) => sessionsAPI.abort(sessionId),
    onSuccess: (_, sessionId) => {
      queryClient.invalidateQueries({ queryKey: sessionKeys.detail(sessionId) });
      queryClient.invalidateQueries({ queryKey: sessionKeys.lists() });
      toast.success('Session aborted');
    },
    onError: (error: Error) => {
      toast.error(error.message || 'Failed to abort session');
    },
  });
}

/**
 * Hook to delete (terminate) a session
 */
export function useDeleteSession() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (sessionId: string) => sessionsAPI.delete(sessionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: sessionKeys.lists() });
      toast.success('Session deleted');
    },
    onError: (error: Error) => {
      toast.error(error.message || 'Failed to delete session');
    },
  });
}
