import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { environmentsAPI } from '../endpoints/environments';
import { harnessKeys } from './useHarnesses';
import type { EnvironmentWrite } from '../types/environment.types';
import { useAuthStore } from '../../store/authStore';

export const environmentKeys = { all: ['environments'] as const };

export function useEnvironments() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  return useQuery({ queryKey: environmentKeys.all, queryFn: environmentsAPI.list, enabled: isAuthenticated });
}

export function useSaveEnvironment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id?: string; body: EnvironmentWrite }) =>
      id ? environmentsAPI.update(id, body) : environmentsAPI.create(body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: environmentKeys.all });
      void queryClient.invalidateQueries({ queryKey: harnessKeys.all }); // drafts show the new settings
    },
  });
}

export function useDeleteEnvironment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => environmentsAPI.delete(id),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: environmentKeys.all }),
  });
}
