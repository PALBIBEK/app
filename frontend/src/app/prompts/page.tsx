/**
 * Prompts: every harness's system prompt in one place. Each harness keeps its prompt in its
 * manifest (the builder's Prompt tab), so this page reads the drafts and links there to edit.
 */

'use client';

import Link from 'next/link';
import { PencilSimple, TextAa, WarningCircle } from '@phosphor-icons/react';

import { AuthGuard } from '@/components/auth/AuthGuard';
import { useHarnesses } from '@/lib/api/hooks/useHarnesses';
import { useManifest } from '@/lib/api/hooks/useManifest';
import type { Harness } from '@/lib/api/types/harness.types';
import { AppShell } from '@/components/layout/AppShell';

function HarnessPrompt({ harness }: { harness: Harness }): JSX.Element {
  const { data, isLoading, error } = useManifest(harness.id);
  const prompt = data?.manifest.prompt;

  return (
    <section data-testid="harness-prompt" className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-base font-semibold text-slate-900">{harness.name}</h2>
        {prompt && <span className="rounded-md bg-slate-100 px-2 py-0.5 text-xs text-slate-600">persona: {prompt.persona}</span>}
        {data && (
          <span className="text-xs text-slate-500">
            {data.published_version != null ? `version ${data.published_version}` : 'not published yet'}
            {data.unpublished_changes ? ' · draft has changes' : ''}
          </span>
        )}
        <Link
          href={`/harness/${harness.id}`}
          className="ml-auto inline-flex items-center gap-1 rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50"
        >
          <PencilSimple size={14} /> Edit prompt
        </Link>
      </div>

      {isLoading && <div className="mt-4 h-16 animate-pulse rounded-xl bg-slate-100" />}
      {error && (
        <p className="mt-4 flex items-center gap-2 text-sm text-red-600">
          <WarningCircle size={16} /> Could not load this harness&apos;s prompt.
        </p>
      )}
      {prompt && prompt.sections.length === 0 && <p className="mt-4 text-sm text-slate-500">No prompt sections yet.</p>}
      {prompt && prompt.sections.length > 0 && (
        <ol className="mt-4 space-y-3">
          {prompt.sections.map((s) => (
            <li key={s.id}>
              <div className="font-mono text-xs text-slate-500">{s.id}</div>
              <p className="mt-1 whitespace-pre-wrap rounded-xl bg-slate-50 px-3 py-2 text-sm text-slate-800">{s.text}</p>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function PromptsContent(): JSX.Element {
  const { data, isLoading, error } = useHarnesses();
  const harnesses = data?.data ?? [];

  return (
    <div className="mx-auto max-w-6xl px-8 py-8">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-accent-50 text-accent-600"><TextAa size={22} weight="bold" /></div>
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Prompts</h1>
          <p className="text-sm text-slate-500">
            The system prompt of each harness, sent in this order. <code>{'{cwd}'}</code> becomes the working directory.
          </p>
        </div>
      </div>

      <div className="mt-6 space-y-4">
        {isLoading && <div className="h-32 animate-pulse rounded-2xl bg-slate-100" />}
        {error && <p className="text-sm text-red-600">Could not load your harnesses.</p>}
        {!isLoading && !error && harnesses.length === 0 && (
          <div className="rounded-2xl border border-dashed border-slate-200 bg-white/50 p-8 text-center text-sm text-slate-500">
            No harnesses yet.{' '}
            <Link href="/harness/new" className="font-medium text-primary-600 hover:text-primary-700">Create one</Link>{' '}
            and write its prompt in the builder.
          </div>
        )}
        {harnesses.map((h) => (
          <HarnessPrompt key={h.id} harness={h} />
        ))}
      </div>
    </div>
  );
}

export default function PromptsPage(): JSX.Element {
  return (
    <AuthGuard>
      <AppShell>
          <PromptsContent />
        </AppShell>
    </AuthGuard>
  );
}
