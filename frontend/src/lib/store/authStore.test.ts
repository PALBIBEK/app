/**
 * authStore.test.ts
 *
 * RED Phase: Write failing tests first
 * Reference: 08-testing-patterns.md (AAA Pattern)
 * Reference: FRONTEND_BUILD_CONFIG.md (Zustand for state)
 *
 * Test Strategy:
 * 1. Test initial state is unauthenticated
 * 2. Test setAuth sets user and access token correctly
 * 3. Test clearAuth clears all auth state
 * 4. Test updateUser partially updates user
 * 5. Test setTokens updates the access token
 * 6. Test that localStorage is never used
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { act } from '@testing-library/react';
import { useAuthStore } from './authStore';

/**
 * authStore.test.ts
 *
 * GREEN Phase: Tests passing with implementation
 * Reference: 08-testing-patterns.md (AAA Pattern)
 * Reference: FRONTEND_BUILD_CONFIG.md (Zustand for state)
 */

/** Calls made on localStorage itself: the store may use sessionStorage (CSRF), never localStorage. */
function localStorageCalls(method: 'setItem' | 'removeItem'): number {
  const spy = Storage.prototype[method] as unknown as { mock: { contexts: unknown[] } };
  return spy.mock.contexts.filter((self) => self === localStorage).length;
}

describe('authStore', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    vi.spyOn(Storage.prototype, 'setItem');
    vi.spyOn(Storage.prototype, 'removeItem');

    // Reset Zustand store to initial state between tests
    act(() => {
      useAuthStore.setState({
        user: null,
        accessToken: null,
        csrfToken: null,
        isAuthenticated: false,
      });
    });
  });

  describe('Initial State', () => {
    it('should initialize with null user', () => {
      // Arrange & Act
      const state = useAuthStore.getState();

      // Assert
      expect(state.user).toBeNull();
    });

    it('should initialize with a null access token', () => {
      // Arrange & Act
      const state = useAuthStore.getState();

      // Assert
      expect(state.accessToken).toBeNull();
    });

    it('should initialize as not authenticated', () => {
      // Arrange & Act
      const state = useAuthStore.getState();

      // Assert
      expect(state.isAuthenticated).toBe(false);
    });
  });

  describe('setAuth', () => {
    it('should set user and access token correctly', () => {
      // Arrange
      const mockUser = {
        id: 'user-123',
        email: 'test@example.com',
        name: 'Test User',
        role: 'member' as const,
      };
      const accessToken = 'access-token-123';

      // Act
      act(() => {
        useAuthStore.getState().setAuth(mockUser, accessToken, 'csrf-123');
      });

      // Assert
      const state = useAuthStore.getState();
      expect(state.user).toEqual(mockUser);
      expect(state.accessToken).toBe(accessToken);
      expect(state.csrfToken).toBe('csrf-123');
      expect(state.isAuthenticated).toBe(true);
    });

    // SECURITY: Tokens are no longer stored in localStorage to prevent XSS attacks
    // This test documents the security decision
    it('should NOT store tokens in localStorage (XSS protection)', () => {
      // Arrange
      const mockUser = {
        id: 'user-123',
        email: 'test@example.com',
        name: 'Test User',
        role: 'member' as const,
      };

      // Act
      act(() => {
        useAuthStore.getState().setAuth(mockUser, 'access-123', 'csrf-123');
      });

      // Assert - localStorage should NOT be called (security fix)
      expect(localStorageCalls('setItem')).toBe(0);
    });
  });

  describe('clearAuth', () => {
    it('should clear all auth state', () => {
      // Arrange
      const mockUser = {
        id: 'user-123',
        email: 'test@example.com',
        name: 'Test User',
        role: 'member' as const,
      };

      // Set auth first
      act(() => {
        useAuthStore.getState().setAuth(mockUser, 'access-123', 'csrf-123');
      });

      // Act
      act(() => {
        useAuthStore.getState().clearAuth();
      });

      // Assert
      const state = useAuthStore.getState();
      expect(state.user).toBeNull();
      expect(state.accessToken).toBeNull();
      expect(state.csrfToken).toBeNull();
      expect(state.isAuthenticated).toBe(false);
    });

    // SECURITY: Tokens are no longer in localStorage (XSS protection)
    it('should NOT interact with localStorage (XSS protection)', () => {
      // Act
      act(() => {
        useAuthStore.getState().clearAuth();
      });

      // Assert - localStorage should NOT be called (security fix)
      expect(localStorageCalls('removeItem')).toBe(0);
    });
  });

  describe('updateUser', () => {
    it('should partially update user data', () => {
      // Arrange
      const mockUser = {
        id: 'user-123',
        email: 'test@example.com',
        name: 'Test User',
        role: 'member' as const,
      };

      act(() => {
        useAuthStore.getState().setAuth(mockUser, 'access-123', 'csrf-123');
      });

      // Act
      act(() => {
        useAuthStore.getState().updateUser({ name: 'Updated Name' });
      });

      // Assert
      const state = useAuthStore.getState();
      expect(state.user?.name).toBe('Updated Name');
      expect(state.user?.email).toBe('test@example.com'); // Should remain unchanged
    });

    it('should not update if user is null', () => {
      // Act - Store is already reset to null in beforeEach
      act(() => {
        useAuthStore.getState().updateUser({ name: 'New Name' });
      });

      // Assert
      const state = useAuthStore.getState();
      expect(state.user).toBeNull();
    });
  });

  describe('setTokens', () => {
    it('should update access token in memory only', () => {
      // Act
      act(() => {
        useAuthStore.getState().setTokens('new-access-token', 'new-csrf');
      });

      // Assert
      const state = useAuthStore.getState();
      expect(state.accessToken).toBe('new-access-token');
      expect(state.csrfToken).toBe('new-csrf');
      // SECURITY: Should NOT store in localStorage
      expect(localStorageCalls('setItem')).toBe(0);
    });
  });
});
