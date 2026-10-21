'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';

import { memoryAPI } from '@/lib/api/endpoints/memory';
import type { MemorySearchRequest, NotebookCreateRequest } from '@/lib/api/types/memory.types';
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
  notebooks: (harnessId: string, archived: boolean) =>
    [...memoryKeys.all, 'notebooks', harnessId, archived] as const,
  facts: (notebookId: string) => [...memoryKeys.all, 'facts', notebookId] as const,
  revisions: (notebookId: string) => [...memoryKeys.all, 'revisions', notebookId] as const,
};

export function useNotebooks(harnessId: string | undefined, archived = false) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  return useQuery({
    queryKey: memoryKeys.notebooks(harnessId ?? '', archived),
    queryFn: () => memoryAPI.listNotebooks(harnessId!, archived),
    enabled: isAuthenticated && Boolean(harnessId),
  });
}

export function useFacts(notebookId: string | undefined) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  return useQuery({
    queryKey: memoryKeys.facts(notebookId ?? ''),
    queryFn: () => memoryAPI.listFacts(notebookId!),
    enabled: isAuthenticated && Boolean(notebookId),
  });
}

export function useRevisions(notebookId: string | undefined) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  return useQuery({
    queryKey: memoryKeys.revisions(notebookId ?? ''),
    queryFn: () => memoryAPI.revisions(notebookId!),
    enabled: isAuthenticated && Boolean(notebookId),
  });
}

/** A mutation on one notebook that refreshes its facts, history and the notebook lists. */
function useNotebookMutation<V>(fn: (vars: V) => Promise<unknown>, done: string, failed: string, id: (vars: V) => string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (_result, vars) => {
      const notebookId = id(vars);
      queryClient.invalidateQueries({ queryKey: memoryKeys.facts(notebookId) });
      queryClient.invalidateQueries({ queryKey: memoryKeys.revisions(notebookId) });
      queryClient.invalidateQueries({ queryKey: [...memoryKeys.all, 'notebooks'] });
      toast.success(done);
    },
    onError: (error: Error) => {
      toast.error(sanitizeErrorMessage(error) || failed);
    },
  });
}

export function useCreateNotebook() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: NotebookCreateRequest) => memoryAPI.createNotebook(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [...memoryKeys.all, 'notebooks'] });
      toast.success('Notebook created');
    },
    onError: (error: Error) => {
      toast.error(sanitizeErrorMessage(error) || 'Failed to create notebook');
    },
  });
}

export const useAddFacts = () =>
  useNotebookMutation(
    (v: { notebookId: string; facts: string[] }) => memoryAPI.addFacts(v.notebookId, v.facts),
    'Saved', 'Failed to save', (v) => v.notebookId,
  );

export const useEditFacts = () =>
  useNotebookMutation(
    (v: { notebookId: string; edits: { id: string; text: string }[] }) => memoryAPI.editFacts(v.notebookId, v.edits),
    'Updated', 'Failed to update', (v) => v.notebookId,
  );

export const useForgetFacts = () =>
  useNotebookMutation(
    (v: { notebookId: string; ids: string[] }) => memoryAPI.forgetFacts(v.notebookId, v.ids),
    'Forgotten', 'Failed to forget', (v) => v.notebookId,
  );

export const useClearNotebook = () =>
  useNotebookMutation((notebookId: string) => memoryAPI.clear(notebookId), 'Cleared', 'Failed to clear', (v) => v);

export const useRestoreNotebook = () =>
  useNotebookMutation(
    (v: { notebookId: string; revision: number }) => memoryAPI.restore(v.notebookId, v.revision),
    'Restored', 'Failed to restore', (v) => v.notebookId,
  );

export function useSearchMemory() {
  return useMutation({
    mutationFn: (body: MemorySearchRequest) => memoryAPI.search(body),
    onError: (error: Error) => {
      toast.error(sanitizeErrorMessage(error) || 'Search failed');
    },
  });
}
