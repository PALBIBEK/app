'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { List, SignOut } from '@phosphor-icons/react';

import { authAPI } from '@/lib/api/endpoints/auth';
import { useAuthStore } from '@/lib/store/authStore';

interface HeaderProps {
  /** Opens the menu drawer on narrow screens. */
  onMenuClick?: () => void;
}

export function Header({ onMenuClick }: HeaderProps): JSX.Element {
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const clearAuth = useAuthStore((s) => s.clearAuth);
  const name = user?.name?.trim() || user?.email || '';
  const initials =
    name
      .split(/[\s@._-]+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0]?.toUpperCase())
      .join('') || '?';

  async function handleLogout(): Promise<void> {
    try {
      await authAPI.logout();
    } catch {
      // The session may already be gone; local state is cleared either way.
    }
    clearAuth();
    router.replace('/login');
  }

  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-line bg-surface px-4 sm:px-6">
      {onMenuClick ? (
        <button
          type="button"
          aria-label="Open menu"
          onClick={onMenuClick}
          className="inline-flex h-9 w-9 items-center justify-center rounded-lg text-ink-2 hover:bg-line-soft md:hidden"
        >
          <List size={20} />
        </button>
      ) : null}
      <span className="font-semibold md:hidden">Prime Agent</span>
      <div className="flex-1" />

      {isAuthenticated && user ? (
        <div className="flex items-center gap-2">
          <div className="hidden text-right sm:block">
            <p className="text-sm font-medium leading-tight">{user.name || user.email}</p>
            {user.name ? <p className="text-xs text-ink-3">{user.email}</p> : null}
          </div>
          <span
            role="img"
            aria-label={`Signed in as ${name}`}
            className="flex h-8 w-8 items-center justify-center rounded-full bg-line text-xs font-semibold text-ink"
          >
            {initials}
          </span>
          <button
            type="button"
            onClick={() => void handleLogout()}
            className="inline-flex h-9 items-center gap-1.5 rounded-lg px-3 text-sm text-ink-2 hover:bg-line-soft hover:text-ink"
          >
            <SignOut size={16} aria-hidden="true" />
            Sign out
          </button>
        </div>
      ) : (
        <Link href="/login" className="inline-flex h-9 items-center rounded-lg px-3 text-sm font-medium text-accent-600 hover:bg-accent-50">
          Sign in
        </Link>
      )}
    </header>
  );
}
