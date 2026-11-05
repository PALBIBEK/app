'use client';

import { useQuery } from '@tanstack/react-query';

import { modelsAPI } from '@/lib/api/endpoints/models';
import { useAuthStore } from '@/lib/store/authStore';

export const modelKeys = {
  all: ['models'] as const,
  choices: () => [...modelKeys.all, 'choices'] as const,
};

/** The models a session can run now, and which vendors are ready. The supervisor re-reads the
 * gateway every five minutes; a minute here keeps the picker close to that. */
export function useModelChoices() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  return useQuery({
    queryKey: modelKeys.choices(),
    queryFn: () => modelsAPI.choices(),
    enabled: isAuthenticated,
    staleTime: 60_000,
  });
}
