/**
 * API Client - Centralized HTTP client with interceptors
 *
 * Features:
 * - Automatic auth token injection
 * - Token refresh on 401
 * - Error handling
 * - TypeScript type safety
 *
 * References:
 * - FRONTEND_BUILD_CONFIG.md (TypeScript patterns)
 * - 06-authentication-and-authorization.md (JWT handling)
 */

import axios, { AxiosInstance, AxiosError, InternalAxiosRequestConfig } from 'axios';

import { useAuthStore } from '../store/authStore';

import { serverMessage } from './errors';

interface AuthRequestConfig extends InternalAxiosRequestConfig {
  _retry?: boolean;
}

export class APIClient {
  private client: AxiosInstance;
  private refreshPromise: Promise<string> | null = null;

  constructor() {
    this.client = axios.create({
      baseURL: process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000/api/v1',
      timeout: 30000,
      withCredentials: true,
      headers: {
        'Content-Type': 'application/json',
      },
    });

    this.setupInterceptors();
  }

  private setupInterceptors(): void {
    // Request interceptor - Add auth token
    this.client.interceptors.request.use(
      (config: InternalAxiosRequestConfig) => {
        const token = this.getToken();
        if (token && config.headers) {
          config.headers.Authorization = `Bearer ${token}`;
        }
        const csrfToken = useAuthStore.getState().csrfToken;
        const requestUrl = config.url ?? '';
        if (
          csrfToken &&
          config.headers &&
          (requestUrl.includes('/auth/refresh') || requestUrl.includes('/auth/logout'))
        ) {
          config.headers['X-CSRF-Token'] = csrfToken;
        }
        return config;
      },
      (error) => Promise.reject(error)
    );

    // Response interceptor - Handle errors and token refresh
    this.client.interceptors.response.use(
      (response) => response,
      async (error: AxiosError) => {
        const originalRequest = error.config as AuthRequestConfig;

        // Handle 401 - Unauthorized
        if (error.response?.status === 401 && originalRequest && !originalRequest._retry) {
          originalRequest._retry = true;

          try {
            const newToken = await this.refreshToken();
            if (originalRequest.headers) {
              originalRequest.headers.Authorization = `Bearer ${newToken}`;
            }
            return this.client(originalRequest);
          } catch (refreshError) {
            // Clear auth and redirect to login
            this.clearAuth();
            if (typeof window !== 'undefined') {
              window.location.href = '/login';
            }
            return Promise.reject(refreshError);
          }
        }

        // Show the server's reason ("Docker is not reachable ...") rather than
        // "Request failed with status code 422"; the response stays on the error.
        const reason = serverMessage(error.response?.data);
        if (reason) error.message = reason;
        return Promise.reject(error);
      }
    );
  }

  private getToken(): string | null {
    return useAuthStore.getState().accessToken;
  }

  private refreshToken(): Promise<string> {
    if (!this.refreshPromise) {
      this.refreshPromise = this.performRefresh().finally(() => {
        this.refreshPromise = null;
      });
    }
    return this.refreshPromise;
  }

  private async performRefresh(): Promise<string> {
    const csrfToken = useAuthStore.getState().csrfToken;
    const response = await axios.post(
      `${this.client.defaults.baseURL}/auth/refresh`,
      undefined,
      {
        withCredentials: true,
        headers: csrfToken ? { 'X-CSRF-Token': csrfToken } : {},
      }
    );

    const accessToken = response.data?.access_token;
    const nextCsrfToken = response.data?.csrf_token;
    if (typeof accessToken !== 'string' || accessToken.length === 0) {
      throw new Error('Refresh response did not include an access token');
    }
    useAuthStore.getState().setTokens(
      accessToken,
      typeof nextCsrfToken === 'string' ? nextCsrfToken : undefined
    );
    return accessToken;
  }

  private clearAuth(): void {
    useAuthStore.getState().clearAuth();
  }

  // HTTP Methods
  async get<T>(url: string, config?: any): Promise<T> {
    const response = await this.client.get<T>(url, config);
    return response.data;
  }

  async post<T>(url: string, data?: any, config?: any): Promise<T> {
    const response = await this.client.post<T>(url, data, config);
    return response.data;
  }

  async put<T>(url: string, data?: any, config?: any): Promise<T> {
    const response = await this.client.put<T>(url, data, config);
    return response.data;
  }

  async patch<T>(url: string, data?: any, config?: any): Promise<T> {
    const response = await this.client.patch<T>(url, data, config);
    return response.data;
  }

  async delete<T>(url: string, config?: any): Promise<T> {
    const response = await this.client.delete<T>(url, config);
    return response.data;
  }
}

export const apiClient = new APIClient();
