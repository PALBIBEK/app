/**
 * auth.ts
 *
 * GREEN Phase: Auth API Endpoints
 * Reference: 06-authentication-and-authorization.md (JWT patterns)
 * Reference: VibeSec Skill (Auth security)
 *
 * SECURITY NOTES:
 * - Passwords transmitted only during login/register (never stored client-side)
 * - Tokens validated with Zod schemas
 * - Server handles all actual authentication logic
 * - CSRF protection required on all auth endpoints
 */

import { apiClient } from '../client';
import {
  LoginRequest,
  RegisterRequest,
  AuthResponse,
  AuthResponseSchema,
  TokenRefreshResponse,
  TokenRefreshResponseSchema,
  User,
  UserSchema,
} from '../types/auth.types';

export const authAPI = {
  /**
   * Login with email and password
   * POST /auth/login
   */
  login: async (credentials: LoginRequest): Promise<AuthResponse> => {
    const response = await apiClient.post<AuthResponse>('/auth/login', credentials);
    // Validate response structure
    return AuthResponseSchema.parse(response);
  },

  /**
   * Register a new user
   * POST /auth/register
   */
  register: async (data: RegisterRequest): Promise<AuthResponse> => {
    const response = await apiClient.post<AuthResponse>('/auth/register', data);
    return AuthResponseSchema.parse(response);
  },

  /**
   * Logout current user
   * POST /auth/logout
   *
   * SECURITY: Server should invalidate refresh token
   */
  logout: async (): Promise<void> => {
    await apiClient.post('/auth/logout');
  },

  /**
   * Refresh access token using refresh token
   * POST /auth/refresh
   *
   * SECURITY: Old refresh token should be invalidated (rotation)
   */
  refreshToken: async (): Promise<TokenRefreshResponse> => {
    const response = await apiClient.post<TokenRefreshResponse>('/auth/refresh');
    return TokenRefreshResponseSchema.parse(response);
  },

  /**
   * Get current authenticated user
   * GET /users/me
   */
  getCurrentUser: async (): Promise<User> => {
    const response = await apiClient.get<User>('/users/me');
    return UserSchema.parse(response);
  },

  /**
   * Request password reset email (backend not implemented — Sprint 5 out of scope)
   * POST /auth/forgot-password
   */
  forgotPassword: async (email: string): Promise<void> => {
    await apiClient.post('/auth/forgot-password', { email });
  },

  /**
   * Reset password with token (backend not implemented — Sprint 5 out of scope)
   * POST /auth/reset-password
   *
   * SECURITY: Token should be single-use and expire quickly
   */
  resetPassword: async (token: string, password: string): Promise<void> => {
    await apiClient.post('/auth/reset-password', { token, password });
  },

  /**
   * Change password for authenticated user
   * POST /users/me/password
   */
  changePassword: async (
    currentPassword: string,
    newPassword: string
  ): Promise<void> => {
    await apiClient.post('/users/me/password', {
      current_password: currentPassword,
      new_password: newPassword,
    });
  },
};
