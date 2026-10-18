/**
 * Memory detail — honest unavailable empty state (D7 acceptance / G6).
 */

'use client';

import { AuthGuard } from '@/components/auth/AuthGuard';
import { MemoryUnavailablePage } from '@/components/memory/MemoryUnavailable';

export default function MemoryDetailPage(): JSX.Element {
  return (
    <AuthGuard>
      <MemoryUnavailablePage />
    </AuthGuard>
  );
}
