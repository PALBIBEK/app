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

export default function RegisterPage(): JSX.Element {
  const router = useRouter();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const setAuth = useAuthStore((s) => s.setAuth);
  const [name, setName] = useState('');
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
      const response = await authAPI.register({ name, email, password });
      setAuth(toStoreUser(response.user), response.access_token, response.csrf_token);
      router.replace('/');
    } catch (err) {
      setError(getApiErrorMessage(err, 'Unable to create account'));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-[100dvh] items-center justify-center bg-canvas px-4">
      <div className="w-full max-w-md rounded-card border border-line bg-surface p-8">
        <div className="mb-8 flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent-500">
            <Command size={18} weight="bold" className="text-white" />
          </div>
          <div>
            <h1 className="text-lg font-semibold text-ink">Create account</h1>
            <p className="text-sm text-ink-2">Harness Lab</p>
          </div>
        </div>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label htmlFor="name" className="block text-sm font-medium text-ink-2">
              Name
            </label>
            <input
              id="name"
              type="text"
              autoComplete="name"
              required
              minLength={2}
              value={name}
              onChange={(e) => setName(e.target.value)}
              className={cn(
                'mt-1 h-11 w-full rounded-xl border border-line bg-canvas px-3 text-sm',
                'focus:border-accent-400 focus:bg-surface focus:outline-none focus:ring-2 focus:ring-accent-500/20'
              )}
            />
          </div>
          <div>
            <label htmlFor="email" className="block text-sm font-medium text-ink-2">
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
                'mt-1 h-11 w-full rounded-xl border border-line bg-canvas px-3 text-sm',
                'focus:border-accent-400 focus:bg-surface focus:outline-none focus:ring-2 focus:ring-accent-500/20'
              )}
            />
          </div>
          <div>
            <label htmlFor="password" className="block text-sm font-medium text-ink-2">
              Password
            </label>
            <input
              id="password"
              type="password"
              autoComplete="new-password"
              required
              minLength={12}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className={cn(
                'mt-1 h-11 w-full rounded-xl border border-line bg-canvas px-3 text-sm',
                'focus:border-accent-400 focus:bg-surface focus:outline-none focus:ring-2 focus:ring-accent-500/20'
              )}
            />
            <p className="mt-1 text-xs text-ink-3">At least 12 characters</p>
          </div>
          {error && (
            <p className="text-sm text-bad" role="alert">
              {error}
            </p>
          )}
          <button
            type="submit"
            disabled={submitting}
            className="h-11 w-full rounded-xl bg-accent-600 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-60"
          >
            {submitting ? 'Creating account…' : 'Register'}
          </button>
        </form>
        <p className="mt-6 text-center text-sm text-ink-2">
          Already have an account?{' '}
          <Link href="/login" className="font-medium text-accent-600 hover:text-accent-700">
            Sign in
          </Link>
        </p>
      </div>
    </div>
  );
}
