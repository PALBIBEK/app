'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { catalogAPI, harnessesAPI } from '@/lib/api/endpoints/harnesses';
import type { Manifest } from '@/lib/api/types/manifest.types';
import { useAuthStore } from '@/lib/store/authStore';

import { harnessKeys } from './useHarnesses';

export const manifestKeys = {
  manifest: (id: string) => [...harnessKeys.detail(id), 'manifest'] as const,
  releases: (id: string) => [...harnessKeys.detail(id), 'releases'] as const,
  catalogTools: ['catalog', 'tools'] as const,
  catalogModels: ['catalog', 'models'] as const,
};

function useEnabled(extra = true): boolean {
  return useAuthStore((s) => s.isAuthenticated) && extra;
}

export function useManifest(id: string | undefined) {
  return useQuery({
    queryKey: manifestKeys.manifest(id ?? ''),
    queryFn: () => harnessesAPI.getManifest(id!),
    enabled: useEnabled(Boolean(id)),
  });
}

export function useReleases(id: string | undefined) {
  return useQuery({
    queryKey: manifestKeys.releases(id ?? ''),
    queryFn: () => harnessesAPI.releases(id!),
    enabled: useEnabled(Boolean(id)),
  });
}

export function useCatalogTools() {
  return useQuery({ queryKey: manifestKeys.catalogTools, queryFn: catalogAPI.tools, enabled: useEnabled(), staleTime: 60_000 });
}

export function useCatalogModels() {
  return useQuery({ queryKey: manifestKeys.catalogModels, queryFn: catalogAPI.models, enabled: useEnabled(), staleTime: 60_000 });
}

/** Errors are left to the caller: the builder shows them on the tab they belong to. */
export function useSaveManifest(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (manifest: Manifest) => harnessesAPI.saveManifest(id, manifest),
    onSuccess: (view) => queryClient.setQueryData(manifestKeys.manifest(id), view),
  });
}

/** Run the publish checks on the saved draft (save first: the server checks what it has). */
export function useCheckDraft(id: string) {
  return useMutation({ mutationFn: () => harnessesAPI.checkDraft(id) });
}

export function usePublishHarness(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => harnessesAPI.publish(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: harnessKeys.detail(id) });
      void queryClient.invalidateQueries({ queryKey: harnessKeys.lists() });
    },
  });
}
