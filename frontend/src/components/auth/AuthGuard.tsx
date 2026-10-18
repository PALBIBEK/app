'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';

import { useAuthStore } from '@/lib/store/authStore';

export function AuthGuard({ children }: { children: React.ReactNode }): JSX.Element | null {
  const router = useRouter();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const sessionReady = useAuthStore((s) => s.sessionReady);

  useEffect(() => {
    if (sessionReady && !isAuthenticated) {
      router.replace('/login');
    }
  }, [sessionReady, isAuthenticated, router]);

  if (!sessionReady || !isAuthenticated) {
    return null;
  }

  return <>{children}</>;
}
