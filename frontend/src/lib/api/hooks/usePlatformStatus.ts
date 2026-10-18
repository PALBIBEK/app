/**
 * Product AI readiness hook (D2).
 */

import { useQuery } from '@tanstack/react-query';

import { platformAPI } from '../endpoints/platform';
import { useAuthStore } from '../../store/authStore';

export const platformKeys = {
  all: ['platform'] as const,
  status: () => [...platformKeys.all, 'status'] as const,
};

export function usePlatformStatus() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  return useQuery({
    queryKey: platformKeys.status(),
    queryFn: () => platformAPI.getStatus(),
    enabled: isAuthenticated,
    staleTime: 5_000,
    refetchOnWindowFocus: true,
  });
}
