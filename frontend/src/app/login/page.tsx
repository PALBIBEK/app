'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Command } from '@phosphor-icons/react';

import { authAPI } from '@/lib/api/endpoints/auth';
import { getApiErrorMessage } from '@/lib/api/errors';
import { toStoreUser } from '@/lib/auth/toStoreUser';
import { useAuthStore } from '@/lib/store/authStore';
import { cn } from '@/lib/utils';

export default function LoginPage(): JSX.Element {
  const router = useRouter();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const setAuth = useAuthStore((s) => s.setAuth);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (isAuthenticated) {
      router.replace('/');
    }
  }, [isAuthenticated, router]);

  async function handleSubmit(event: React.FormEvent): Promise<void> {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const response = await authAPI.login({ email, password });
      setAuth(toStoreUser(response.user), response.access_token, response.csrf_token);
      router.replace('/');
    } catch (err) {
      setError(getApiErrorMessage(err, 'Unable to sign in'));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-[100dvh] items-center justify-center bg-gradient-subtle px-4">
      <div className="w-full max-w-md rounded-2xl border border-slate-200/80 bg-white p-8 shadow-card">
        <div className="mb-8 flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-primary-500 to-primary-600 shadow-sm shadow-primary-500/20">
            <Command size={18} weight="bold" className="text-white" />
          </div>
          <div>
            <h1 className="text-lg font-semibold text-slate-900">Sign in</h1>
            <p className="text-sm text-slate-500">Harness Lab</p>
          </div>
        </div>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label htmlFor="email" className="block text-sm font-medium text-slate-700">
              Email
            </label>
            <input
              id="email"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className={cn(
                'mt-1 h-11 w-full rounded-xl border border-slate-200 bg-slate-50 px-3 text-sm',
                'focus:border-primary-400 focus:bg-white focus:outline-none focus:ring-2 focus:ring-primary-500/20'
              )}
            />
          </div>
          <div>
            <label htmlFor="password" className="block text-sm font-medium text-slate-700">
              Password
            </label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              required
              minLength={12}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className={cn(
                'mt-1 h-11 w-full rounded-xl border border-slate-200 bg-slate-50 px-3 text-sm',
                'focus:border-primary-400 focus:bg-white focus:outline-none focus:ring-2 focus:ring-primary-500/20'
              )}
            />
            <p className="mt-1 text-xs text-slate-400">At least 12 characters</p>
          </div>
          {error && (
            <p className="text-sm text-red-600" role="alert">
              {error}
            </p>
          )}
          <button
            type="submit"
            disabled={submitting}
            className="h-11 w-full rounded-xl bg-primary-600 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-60"
          >
            {submitting ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
        <p className="mt-6 text-center text-sm text-slate-500">
          No account?{' '}
          <Link href="/register" className="font-medium text-primary-600 hover:text-primary-700">
            Create one
          </Link>
        </p>
      </div>
    </div>
  );
}
