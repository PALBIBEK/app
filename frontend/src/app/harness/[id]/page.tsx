/**
 * Harness page: the manifest builder.
 *
 * Edit the draft on its tabs (prompt, tools, model, sandbox, memory, policy), publish
 * it as a new version, and test it. Each version is immutable in the supervisor's
 * catalog; sessions keep running the version they started on.
 */

'use client';

import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { ArrowLeft, Cube, SpinnerGap, Trash, WarningCircle } from '@phosphor-icons/react';

import { AuthGuard } from '@/components/auth/AuthGuard';
import { HarnessBuilder } from '@/components/harness/builder/HarnessBuilder';
import { AiPlatformBanner } from '@/components/platform/AiPlatformBanner';
import { useDeleteHarness, useHarness } from '@/lib/api/hooks/useHarnesses';
import { AppShell } from '@/components/layout/AppShell';

function HarnessPageContent(): JSX.Element {
  const params = useParams();
  const router = useRouter();
  const id = typeof params.id === 'string' ? params.id : '';
  const { data: harness, isLoading, error } = useHarness(id);
  const deleteHarness = useDeleteHarness();

  if (isLoading) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center gap-3 text-slate-500">
        <SpinnerGap size={24} className="animate-spin" />
        Loading harness…
      </div>
    );
  }

  if (error || !harness) {
    return (
      <div className="mx-auto mt-16 max-w-lg rounded-2xl border border-red-200 bg-red-50/80 p-8 text-center">
        <WarningCircle size={32} className="mx-auto text-red-500" />
        <p className="mt-3 text-sm font-semibold text-red-800">This harness could not be loaded.</p>
        <Link href="/" className="mt-4 inline-flex text-sm font-medium text-primary-600 hover:text-primary-700">
          Back to harnesses
        </Link>
      </div>
    );
  }

  async function handleDelete(): Promise<void> {
    if (!window.confirm(`Delete harness “${harness!.name}”? This cannot be undone.`)) return;
    await deleteHarness.mutateAsync(harness!.id);
    router.push('/');
  }

  return (
    <div className="mx-auto max-w-6xl px-8 py-8">
      <Link href="/" className="group inline-flex items-center gap-2 text-sm font-medium text-slate-500 hover:text-slate-700">
        <ArrowLeft size={16} className="transition-transform group-hover:-translate-x-1" />
        Back to harnesses
      </Link>

      <div className="mt-6 flex items-start gap-4">
        <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-br from-secondary-400 to-secondary-600 shadow-lg shadow-secondary-500/25">
          <Cube size={24} weight="fill" className="text-white" />
        </div>
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">{harness.name}</h1>
          {harness.description ? <p className="mt-1 text-sm text-slate-500">{harness.description}</p> : null}
        </div>
      </div>

      <div className="mt-4">
        <AiPlatformBanner />
      </div>

      <HarnessBuilder harness={harness} />

      <section className="mt-8 flex items-center justify-between gap-4 rounded-2xl border border-red-200 bg-red-50/50 p-5">
        <div>
          <p className="text-sm font-semibold text-red-700">Delete harness</p>
          <p className="mt-0.5 text-xs text-red-600">Its published versions stay in the catalog for sessions already running on them.</p>
        </div>
        <button
          type="button"
          onClick={() => void handleDelete()}
          disabled={deleteHarness.isPending}
          className="inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium text-red-600 hover:bg-red-100 disabled:opacity-60"
        >
          <Trash size={16} />
          {deleteHarness.isPending ? 'Deleting…' : 'Delete'}
        </button>
      </section>
    </div>
  );
}

export default function HarnessPage(): JSX.Element {
  return (
    <AuthGuard>
      <AppShell>
          <HarnessPageContent />
        </AppShell>
    </AuthGuard>
  );
}
