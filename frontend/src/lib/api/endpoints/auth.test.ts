/**
 * auth.test.ts
 *
 * RED Phase: Write failing tests first
 * Reference: 08-testing-patterns.md (AAA Pattern)
 * Reference: 06-authentication-and-authorization.md
 *
 * Test Strategy:
 * 1. Test login with valid credentials
 * 2. Test login validates response with Zod
 * 3. Test register new user
 * 4. Test logout
 * 5. Test token refresh
 * 6. Test get current user
 * 7. Test password reset flow
 */

import { describe, it, expect, beforeEach, vi, Mock } from 'vitest';
import { apiClient } from '../client';
import { authAPI } from './auth';
import type { AuthResponse, User } from '../types/auth.types';

// Mock the apiClient
vi.mock('../client', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}));

// Test data factory
const createMockUser = (overrides: Partial<User> = {}): User => ({
  id: 'user-123',
  email: 'test@example.com',
  name: 'Test User',
  role: 'member',
  ...overrides,
});

const createMockAuthResponse = (): AuthResponse => ({
  user: createMockUser(),
  access_token: 'access-token-123',
  token_type: 'bearer',
  csrf_token: 'csrf-token-123',
});

describe('authAPI', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('login', () => {
    it('should login with valid credentials', async () => {
      // Arrange
      const credentials = { email: 'test@example.com', password: 'password123' };
      const mockResponse = createMockAuthResponse();
      (apiClient.post as Mock).mockResolvedValue(mockResponse);

      // Act
      const result = await authAPI.login(credentials);

      // Assert
      expect(apiClient.post).toHaveBeenCalledWith('/auth/login', credentials);
      expect(result.user.email).toBe('test@example.com');
      expect(result.access_token).toBe('access-token-123');
    });

    it('should validate response with Zod schema', async () => {
      // Arrange - Invalid response missing required field
      const invalidResponse = {
        user: { id: 'user-123' }, // Missing required fields
        access_token: 'token',
        refresh_token: 'refresh',
        token_type: 'bearer',
      };
      (apiClient.post as Mock).mockResolvedValue(invalidResponse);

      // Act & Assert
      await expect(
        authAPI.login({ email: 'test@example.com', password: 'password123' })
      ).rejects.toThrow();
    });
  });

  describe('register', () => {
    it('should register a new user', async () => {
      // Arrange
      const registerData = {
        name: 'New User',
        email: 'new@example.com',
        password: 'password123',
      };
      const mockResponse = createMockAuthResponse();
      mockResponse.user.name = 'New User';
      mockResponse.user.email = 'new@example.com';
      (apiClient.post as Mock).mockResolvedValue(mockResponse);

      // Act
      const result = await authAPI.register(registerData);

      // Assert
      expect(apiClient.post).toHaveBeenCalledWith('/auth/register', registerData);
      expect(result.user.name).toBe('New User');
    });
  });

  describe('logout', () => {
    it('should call logout endpoint', async () => {
      // Arrange
      (apiClient.post as Mock).mockResolvedValue(undefined);

      // Act
      await authAPI.logout();

      // Assert
      expect(apiClient.post).toHaveBeenCalledWith('/auth/logout');
    });
  });

  describe('refreshToken', () => {
    it('should refresh access token', async () => {
      // Arrange
      const mockResponse = {
        access_token: 'new-access-token',
        token_type: 'bearer',
        csrf_token: 'new-csrf-token',
      };
      (apiClient.post as Mock).mockResolvedValue(mockResponse);

      // Act
      const result = await authAPI.refreshToken();

      // Assert
      expect(apiClient.post).toHaveBeenCalledWith('/auth/refresh');
      expect(result.access_token).toBe('new-access-token');
    });
  });

  describe('getCurrentUser', () => {
    it('should fetch current user', async () => {
      // Arrange
      const mockUser = createMockUser();
      (apiClient.get as Mock).mockResolvedValue(mockUser);

      // Act
      const result = await authAPI.getCurrentUser();

      // Assert
      expect(apiClient.get).toHaveBeenCalledWith('/users/me');
      expect(result.id).toBe('user-123');
    });
  });

  describe('Password Reset Flow', () => {
    it('should request password reset', async () => {
      // Arrange
      (apiClient.post as Mock).mockResolvedValue({ message: 'Email sent' });

      // Act
      await authAPI.forgotPassword('test@example.com');

      // Assert
      expect(apiClient.post).toHaveBeenCalledWith('/auth/forgot-password', {
        email: 'test@example.com',
      });
    });

    it('should reset password with token', async () => {
      // Arrange
      (apiClient.post as Mock).mockResolvedValue({ message: 'Password reset' });

      // Act
      await authAPI.resetPassword('reset-token-123', 'newpassword123');

      // Assert
      expect(apiClient.post).toHaveBeenCalledWith('/auth/reset-password', {
        token: 'reset-token-123',
        password: 'newpassword123',
      });
    });

    it('should change password', async () => {
      // Arrange
      (apiClient.post as Mock).mockResolvedValue({ message: 'Password changed' });

      // Act
      await authAPI.changePassword('oldpass123', 'newpass123');

      // Assert
      expect(apiClient.post).toHaveBeenCalledWith('/users/me/password', {
        current_password: 'oldpass123',
        new_password: 'newpass123',
      });
    });
  });

  describe('deleteAccount', () => {
    it('sends the password with DELETE /users/me', async () => {
      (apiClient.delete as Mock).mockResolvedValue(undefined);
      await authAPI.deleteAccount('my long password');
      expect(apiClient.delete).toHaveBeenCalledWith('/users/me', { data: { password: 'my long password' } });
    });
  });
});
