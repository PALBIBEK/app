/**
 * Memory library — honest unavailable empty state (D7 acceptance / G6).
 * Live BFF hooks live under d7_production reopen path.
 */

'use client';

import { AuthGuard } from '@/components/auth/AuthGuard';
import { MemoryUnavailablePage } from '@/components/memory/MemoryUnavailable';

export default function MemoryPage(): JSX.Element {
  return (
    <AuthGuard>
      <MemoryUnavailablePage />
    </AuthGuard>
  );
}
