'use client';

import Link from 'next/link';

import { AuthGuard } from '@/components/auth/AuthGuard';
import { AppShell } from '@/components/layout/AppShell';
import { ModelProviders } from '@/components/settings/ModelProviders';

export default function ModelSettingsPage(): JSX.Element {
  return (
    <AuthGuard>
      <AppShell>
        <div className="mx-auto max-w-4xl px-8 py-8">
          <Link href="/settings" className="text-sm text-accent-600 hover:underline">
            Settings
          </Link>
          <h1 className="mt-2 text-2xl font-semibold tracking-tight text-ink">Model providers</h1>
          <div className="mt-6">
            <ModelProviders />
          </div>
        </div>
      </AppShell>
    </AuthGuard>
  );
}
