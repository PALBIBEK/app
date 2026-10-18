'use client';

import { QueryClientProvider } from '@tanstack/react-query';
import { Toaster } from 'sonner';

import { AuthBootstrap } from '@/components/auth/AuthBootstrap';
import { ErrorBoundary } from '@/components/errors';
import { queryClient } from '@/lib/api/queryClient';

export function Providers({ children }: { children: React.ReactNode }): JSX.Element {
  return (
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <AuthBootstrap>{children}</AuthBootstrap>
        <Toaster richColors position="top-right" />
      </QueryClientProvider>
    </ErrorBoundary>
  );
}
