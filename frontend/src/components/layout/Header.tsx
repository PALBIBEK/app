'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Bell, CaretDown, Command, MagnifyingGlass } from '@phosphor-icons/react';

import { authAPI } from '@/lib/api/endpoints/auth';
import { useAuthStore } from '@/lib/store/authStore';
import { cn } from '@/lib/utils';

interface HeaderProps {
  onMenuClick?: () => void;
}

export function Header(_props: HeaderProps): JSX.Element {
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const clearAuth = useAuthStore((s) => s.clearAuth);
  const initial = (user?.name?.trim()?.[0] || user?.email?.[0] || '?').toUpperCase();

  async function handleLogout(): Promise<void> {
    try {
      await authAPI.logout();
    } catch {
      // Cookie/session may already be gone; still clear local state.
    }
    clearAuth();
    router.replace('/login');
  }

  return (
    <header className="border-b border-slate-200 bg-white/90 backdrop-blur">
      <div className="flex h-14 items-center justify-between gap-4 px-4 sm:px-6">
        <div className="flex items-center gap-3">
          <Link href="/" className="flex items-center gap-2 font-semibold text-slate-900">
            <span className="inline-flex h-8 w-8 items-center justify-center rounded-lg bg-slate-900 text-white">
              <Command size={16} weight="bold" />
            </span>
            <span className="hidden sm:inline">Prime Agent</span>
          </Link>
          <div className="hidden items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-sm text-slate-500 md:flex">
            <MagnifyingGlass size={14} />
            <span>Search</span>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            type="button"
            className={cn(
              'rounded-lg p-2 text-slate-500',
              'hover:bg-slate-100 focus:outline-none focus:ring-2 focus:ring-primary-500/20'
            )}
            aria-label="Notifications"
          >
            <Bell size={18} />
          </button>

          {isAuthenticated && user ? (
            <div className="flex items-center gap-2">
              <div className="flex items-center gap-2.5 rounded-lg px-2 py-1.5">
                <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-slate-700 to-slate-800 text-sm font-medium text-white shadow-sm">
                  {initial}
                </div>
                <div className="hidden text-left sm:block">
                  <p className="text-sm font-medium text-slate-700">{user.name}</p>
                  <p className="text-xs text-slate-500">{user.email}</p>
                </div>
                <CaretDown size={14} weight="bold" className="text-slate-400" />
              </div>
              <button
                type="button"
                onClick={() => void handleLogout()}
                className="rounded-lg px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-100"
              >
                Sign out
              </button>
            </div>
          ) : (
            <Link
              href="/login"
              className="rounded-lg px-3 py-1.5 text-sm font-medium text-primary-600 hover:bg-primary-50"
            >
              Sign in
            </Link>
          )}
        </div>
      </div>
    </header>
  );
}
