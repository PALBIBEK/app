'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { modelProvidersAPI } from '@/lib/api/endpoints/modelProviders';
import { useAuthStore } from '@/lib/store/authStore';

export const modelProviderKeys = {
  all: ['model-providers'] as const,
};

/** Only owners and admins may read or change them (the backend enforces it too). */
export function canManageModelProviders(role: string | undefined): boolean {
  return role === 'owner' || role === 'admin';
}

export function useModelProviders() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const role = useAuthStore((s) => s.user?.role);
  return useQuery({
    queryKey: modelProviderKeys.all,
    queryFn: () => modelProvidersAPI.list(),
    enabled: isAuthenticated && canManageModelProviders(role),
  });
}

/** After a change, what sessions can pick changes too: the model choices are read again. */
function useInvalidate() {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: modelProviderKeys.all });
    void qc.invalidateQueries({ queryKey: ['models'] });
  };
}

export function useSetModelProviderKey() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ vendor, key }: { vendor: string; key: string }) => modelProvidersAPI.setKey(vendor, key),
    onSuccess: invalidate,
  });
}

export function useRemoveModelProviderKey() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (vendor: string) => modelProvidersAPI.removeKey(vendor),
    onSuccess: invalidate,
  });
}
