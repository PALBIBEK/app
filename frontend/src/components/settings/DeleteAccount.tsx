'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';

import { Button } from '@/components/ui';
import { authAPI } from '@/lib/api/endpoints/auth';
import { useAuthStore } from '@/lib/store/authStore';
import { cn } from '@/lib/utils';

/**
 * Deleting the account, confirmed by its password: every session, file, memory and
 * agent of it is erased, and the person is signed out. Kept when anything cannot be
 * erased yet, so it can be tried again.
 */
export function DeleteAccount(): JSX.Element {
  const router = useRouter();
  const clearAuth = useAuthStore((s) => s.clearAuth);
  const [password, setPassword] = useState('');
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function remove(event: React.FormEvent): Promise<void> {
    event.preventDefault();
    setDeleting(true);
    setError(null);
    try {
      await authAPI.deleteAccount(password);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The account could not be deleted');
      setDeleting(false);
      return;
    }
    clearAuth();
    router.replace('/login');
  }

  return (
    <form onSubmit={(e) => void remove(e)} className="rounded-lg border border-red-200 p-4" aria-labelledby="delete-account">
      <h4 id="delete-account" className="text-sm font-semibold text-bad">
        Delete account
      </h4>
      <p className="mt-1 text-sm text-ink-2">
        Erases your sessions and their files, your long-term memory, your workspaces and your agents, for good. Workspaces
        you share with others are handed to them.
      </p>
      <label htmlFor="delete-account-password" className="mt-3 block text-sm font-medium text-ink-2">
        Your password
      </label>
      <div className="mt-1 flex flex-wrap gap-2">
        <input
          id="delete-account-password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className={cn(
            'h-10 min-w-0 flex-1 rounded-lg border border-line bg-canvas px-3 text-sm',
            'focus:border-accent-400 focus:bg-surface focus:outline-none focus:ring-2 focus:ring-accent-500/20',
          )}
        />
        <Button type="submit" variant="danger" disabled={deleting || !password}>
          {deleting ? 'Deleting…' : 'Delete my account'}
        </Button>
      </div>
      {error ? (
        <p className="mt-2 text-sm text-bad" role="alert">
          {error}
        </p>
      ) : null}
    </form>
  );
}
