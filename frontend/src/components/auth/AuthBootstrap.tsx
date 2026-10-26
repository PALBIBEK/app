'use client';

import { useEffect } from 'react';

import { authAPI } from '@/lib/api/endpoints/auth';
import { toStoreUser } from '@/lib/auth/toStoreUser';
import { useAuthStore } from '@/lib/store/authStore';

type Restored = { user: Awaited<ReturnType<typeof authAPI.getCurrentUser>>; access: string; csrf: string };

/**
 * One restore at a time. Each refresh rotates the refresh cookie, so a second
 * concurrent refresh (React runs effects twice in development) presents a spent
 * token, gets a 401, and would sign the user out.
 */
let restoring: Promise<Restored> | null = null;

export function restoreOnce(): Promise<Restored> {
  if (!restoring) {
    restoring = (async () => {
      const refreshed = await authAPI.refreshToken();
      useAuthStore.getState().setTokens(refreshed.access_token, refreshed.csrf_token);
      const user = await authAPI.getCurrentUser();
      return { user, access: refreshed.access_token, csrf: refreshed.csrf_token };
    })().finally(() => {
      restoring = null;
    });
  }
  return restoring;
}

/**
 * Restore a session after a full reload: refresh cookie (HttpOnly) + CSRF from sessionStorage.
 */
export function AuthBootstrap({ children }: { children: React.ReactNode }): JSX.Element {
  const sessionReady = useAuthStore((s) => s.sessionReady);

  useEffect(() => {
    let cancelled = false;

    async function restoreSession(): Promise<void> {
      const store = useAuthStore.getState();
      store.hydrateCsrfFromSession();

      if (store.accessToken && store.isAuthenticated) {
        store.markSessionReady();
        return;
      }

      const csrfToken = useAuthStore.getState().csrfToken;
      if (!csrfToken) {
        store.markSessionReady();
        return;
      }

      try {
        const { user, access, csrf } = await restoreOnce();
        if (cancelled) {
          return;
        }
        useAuthStore.getState().setAuth(toStoreUser(user), access, csrf);
      } catch {
        if (!cancelled) {
          useAuthStore.getState().clearAuth();
        }
      } finally {
        if (!cancelled) {
          useAuthStore.getState().markSessionReady();
        }
      }
    }

    void restoreSession();
    return () => {
      cancelled = true;
    };
  }, []);

  if (!sessionReady) {
    return (
      <div className="flex min-h-[100dvh] items-center justify-center bg-canvas text-sm text-ink-2">
        Loading…
      </div>
    );
  }

  return <>{children}</>;
}
