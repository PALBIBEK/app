/**
 * New memory — honest unavailable empty state (D7 acceptance / G6).
 */

'use client';

import { AuthGuard } from '@/components/auth/AuthGuard';
import { MemoryUnavailablePage } from '@/components/memory/MemoryUnavailable';

export default function NewMemoryPage(): JSX.Element {
  return (
    <AuthGuard>
      <MemoryUnavailablePage />
    </AuthGuard>
  );
}
