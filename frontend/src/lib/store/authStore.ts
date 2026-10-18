/**
 * authStore.ts
 *
 * SECURITY NOTES (VibeSec Audit):
 * - Access tokens are stored in memory only, NOT localStorage (XSS protection)
 * - CSRF may be persisted in sessionStorage so refresh works after full reload
 * - Client-side role is for UI only; server MUST re-validate on every request
 */

import { create } from 'zustand';

/** CSRF only — needed to call refresh after a full page reload. Never store the access token here. */
export const CSRF_STORAGE_KEY = 'prime_agent_csrf';

export interface AuthUser {
  id: string;
  email: string;
  name: string;
  // SECURITY: Client-side role is for UI display only
  // Server MUST validate permissions on every request
  role: 'owner' | 'admin' | 'member';
  avatar?: string;
}

function persistCsrf(csrfToken: string | null): void {
  if (typeof window === 'undefined') {
    return;
  }
  if (csrfToken) {
    sessionStorage.setItem(CSRF_STORAGE_KEY, csrfToken);
  } else {
    sessionStorage.removeItem(CSRF_STORAGE_KEY);
  }
}

interface AuthState {
  user: AuthUser | null;
  // SECURITY: Access token stored in memory only, not persisted
  accessToken: string | null;
  csrfToken: string | null;
  isAuthenticated: boolean;
  sessionReady: boolean;

  // Actions
  setAuth: (user: AuthUser, accessToken: string, csrfToken: string) => void;
  clearAuth: () => void;
  updateUser: (user: Partial<AuthUser>) => void;
  setTokens: (accessToken: string, csrfToken?: string) => void;
  markSessionReady: () => void;
  hydrateCsrfFromSession: () => void;
}

// SECURITY: No persist middleware for the access token
export const useAuthStore = create<AuthState>()((set, get) => ({
  user: null,
  accessToken: null,
  csrfToken: null,
  isAuthenticated: false,
  sessionReady: false,

  hydrateCsrfFromSession: () => {
    if (typeof window === 'undefined') {
      return;
    }
    const stored = sessionStorage.getItem(CSRF_STORAGE_KEY);
    if (stored && !get().csrfToken) {
      set({ csrfToken: stored });
    }
  },

  markSessionReady: () => set({ sessionReady: true }),

  setAuth: (user, accessToken, csrfToken) => {
    persistCsrf(csrfToken);
    set({
      user,
      accessToken,
      csrfToken,
      isAuthenticated: true,
    });
  },

  clearAuth: () => {
    persistCsrf(null);
    set({
      user: null,
      accessToken: null,
      csrfToken: null,
      isAuthenticated: false,
    });
  },

  updateUser: (userData) =>
    set((state) => ({
      user: state.user ? { ...state.user, ...userData } : null,
    })),

  setTokens: (accessToken, csrfToken) => {
    const nextCsrf = csrfToken ?? get().csrfToken;
    persistCsrf(nextCsrf);
    set({
      accessToken,
      csrfToken: nextCsrf,
    });
  },
}));
