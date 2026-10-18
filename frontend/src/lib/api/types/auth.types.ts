/**
 * Auth Type Definitions
 *
 * References:
 * - 06-authentication-and-authorization.md (JWT patterns)
 * - VibeSec Skill (Auth security best practices)
 *
 * SECURITY NOTES:
 * - Passwords are NEVER stored or transmitted in plain text after login
 * - Tokens should be stored in httpOnly cookies (not localStorage)
 * - All auth endpoints must use HTTPS in production
 */

import { z } from 'zod';

// Login request schema
export const LoginRequestSchema = z.object({
  email: z.string().email('Invalid email address'),
  password: z.string().min(12, 'Password must be at least 12 characters'),
});

// Registration request schema
export const RegisterRequestSchema = z.object({
  name: z.string().min(2, 'Name must be at least 2 characters'),
  email: z.string().email('Invalid email address'),
  password: z.string().min(12, 'Password must be at least 12 characters'),
});

// User schema (returned from auth endpoints)
export const UserSchema = z.object({
  id: z.string(),
  email: z.string().email(),
  name: z.string(),
  role: z.enum(['owner', 'admin', 'member']),
  avatar: z.string().optional(),
  created_at: z.string().datetime().optional(),
});

// Auth response schema
export const AuthResponseSchema = z.object({
  user: UserSchema,
  access_token: z.string(),
  token_type: z.literal('bearer'),
  csrf_token: z.string(),
});

// Token refresh response
export const TokenRefreshResponseSchema = z.object({
  access_token: z.string(),
  token_type: z.literal('bearer'),
  csrf_token: z.string(),
});

// TypeScript types
export type LoginRequest = z.infer<typeof LoginRequestSchema>;
export type RegisterRequest = z.infer<typeof RegisterRequestSchema>;
export type User = z.infer<typeof UserSchema>;
export type AuthResponse = z.infer<typeof AuthResponseSchema>;
export type TokenRefreshResponse = z.infer<typeof TokenRefreshResponseSchema>;

// Password reset types (not Zod validated as they're simpler)
export interface ForgotPasswordRequest {
  email: string;
}

export interface ResetPasswordRequest {
  token: string;
  password: string;
}

export interface ChangePasswordRequest {
  current_password: string;
  new_password: string;
}
