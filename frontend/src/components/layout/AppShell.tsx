'use client';

import { useState } from 'react';

import { AuthGuard } from '@/components/auth/AuthGuard';
import { cn } from '@/lib/utils';

import { Header } from './Header';
import { Sidebar } from './Sidebar';

interface AppShellProps {
  children: React.ReactNode;
  /** Classes for the scrolling main area (a full-height page passes its own). */
  mainClassName?: string;
  /** Full-height pages (a live session) fill the window and scroll inside. */
  fullHeight?: boolean;
}

/**
 * Every signed-in page: the grouped menu on the left, the account bar on top,
 * the page in a scrolling main area. Signed-out visitors are sent to sign in.
 */
export function AppShell({ children, mainClassName, fullHeight = false }: AppShellProps): JSX.Element {
  const [menuOpen, setMenuOpen] = useState(false);
  return (
    <AuthGuard>
      <div className={cn('flex bg-canvas', fullHeight ? 'h-[100dvh]' : 'min-h-[100dvh]')}>
        <Sidebar open={menuOpen} onClose={() => setMenuOpen(false)} />
        <div className="flex min-w-0 flex-1 flex-col">
          <Header onMenuClick={() => setMenuOpen(true)} />
          <main className={cn('min-h-0 flex-1 overflow-auto', mainClassName)}>{children}</main>
        </div>
      </div>
    </AuthGuard>
  );
}

/** The page's content column: the design's 1440 px max, 24 px gutters. */
export function PageBody({ children, className }: { children: React.ReactNode; className?: string }): JSX.Element {
  return (
    <div className={cn('mx-auto flex w-full max-w-[1440px] flex-col gap-[18px] px-4 pb-8 pt-6 sm:px-6', className)}>
      {children}
    </div>
  );
}
