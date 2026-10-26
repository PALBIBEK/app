/**
 * Hooks for the approvals inbox, sandboxes, usage, runners, secrets and the fleet.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { operateAPI } from '../endpoints/operate';
import type { ArtifactKind, UsageBy } from '../types/operate.types';
import { useAuthStore } from '../../store/authStore';

export const operateKeys = {
  all: ['operate'] as const,
  inbox: () => [...operateKeys.all, 'inbox'] as const,
  sandboxes: (sessionId?: string, includeEnded?: boolean) =>
    [...operateKeys.all, 'sandboxes', sessionId ?? 'all', !!includeEnded] as const,
  usage: (by: UsageBy, sinceMs?: number, untilMs?: number) =>
    [...operateKeys.all, 'usage', by, sinceMs ?? null, untilMs ?? null] as const,
  runners: () => [...operateKeys.all, 'runners'] as const,
  secrets: () => [...operateKeys.all, 'secrets'] as const,
  workers: () => [...operateKeys.all, 'workers'] as const,
  rollout: () => [...operateKeys.all, 'rollout'] as const,
};

function useAuthed() {
  return useAuthStore((s) => s.isAuthenticated);
}

/** Approvals waiting on the user; polled, since a run can ask at any moment. */
export function useApprovalInbox() {
  const enabled = useAuthed();
  return useQuery({
    queryKey: operateKeys.inbox(),
    queryFn: () => operateAPI.approvalInbox(),
    enabled,
    refetchInterval: 10_000,
  });
}

export function useSandboxes(opts: { sessionId?: string; includeEnded?: boolean; poll?: boolean } = {}) {
  const enabled = useAuthed();
  return useQuery({
    queryKey: operateKeys.sandboxes(opts.sessionId, opts.includeEnded),
    queryFn: () => operateAPI.sandboxes(opts),
    enabled,
    refetchInterval: opts.poll === false ? false : 15_000,
  });
}

export function useUsage(by: UsageBy, sinceMs?: number, untilMs?: number) {
  const enabled = useAuthed();
  return useQuery({
    queryKey: operateKeys.usage(by, sinceMs, untilMs),
    queryFn: () => operateAPI.usage({ by, sinceMs, untilMs }),
    enabled,
    staleTime: 30_000,
  });
}

export function useRunners() {
  const enabled = useAuthed();
  return useQuery({ queryKey: operateKeys.runners(), queryFn: () => operateAPI.runners(), enabled, refetchInterval: 15_000 });
}

export function useRegisterRunner() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => operateAPI.registerRunner(name),
    onSuccess: () => qc.invalidateQueries({ queryKey: operateKeys.runners() }),
  });
}

export function useRemoveRunner() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => operateAPI.removeRunner(name),
    onSuccess: () => qc.invalidateQueries({ queryKey: operateKeys.runners() }),
  });
}

export function useSecretNames() {
  const enabled = useAuthed();
  return useQuery({ queryKey: operateKeys.secrets(), queryFn: () => operateAPI.secretNames(), enabled });
}

export function usePutSecret() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, value }: { name: string; value: string }) => operateAPI.putSecret(name, value),
    onSuccess: () => qc.invalidateQueries({ queryKey: operateKeys.secrets() }),
  });
}

export function useDeleteSecret() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => operateAPI.deleteSecret(name),
    onSuccess: () => qc.invalidateQueries({ queryKey: operateKeys.secrets() }),
  });
}

export function useCheckArtifact() {
  return useMutation({
    mutationFn: ({ kind, body }: { kind: ArtifactKind; body: Record<string, unknown> }) =>
      operateAPI.checkArtifact(kind, body),
  });
}

/** The worker fleet; admins only (a member gets 403, which the page shows as such). */
export function useFleet(enabledForRole: boolean) {
  const authed = useAuthed();
  const enabled = authed && enabledForRole;
  const workers = useQuery({ queryKey: operateKeys.workers(), queryFn: () => operateAPI.workers(), enabled, refetchInterval: 10_000, retry: false });
  const rollout = useQuery({ queryKey: operateKeys.rollout(), queryFn: () => operateAPI.rollout(), enabled, refetchInterval: 30_000, retry: false });
  return { workers, rollout };
}
