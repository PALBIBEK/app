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
    <section data-testid="harness-prompt" className="rounded-card border border-line bg-surface p-5">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-base font-semibold text-ink">{harness.name}</h2>
        {prompt && <span className="rounded-md bg-line-soft px-2 py-0.5 text-xs text-ink-2">persona: {prompt.persona}</span>}
        {data && (
          <span className="text-xs text-ink-2">
            {data.published_version != null ? `version ${data.published_version}` : 'not published yet'}
            {data.unpublished_changes ? ' · draft has changes' : ''}
          </span>
        )}
        <Link
          href={`/harness/${harness.id}`}
          className="ml-auto inline-flex items-center gap-1 rounded-lg border border-line px-3 py-1.5 text-sm text-ink-2 hover:bg-canvas"
        >
          <PencilSimple size={14} /> Edit prompt
        </Link>
      </div>

      {isLoading && <div className="mt-4 h-16 animate-pulse rounded-xl bg-line-soft" />}
      {error && (
        <p className="mt-4 flex items-center gap-2 text-sm text-bad">
          <WarningCircle size={16} /> Could not load this harness&apos;s prompt.
        </p>
      )}
      {prompt && prompt.sections.length === 0 && <p className="mt-4 text-sm text-ink-2">No prompt sections yet.</p>}
      {prompt && prompt.sections.length > 0 && (
        <ol className="mt-4 space-y-3">
          {prompt.sections.map((s) => (
            <li key={s.id}>
              <div className="font-mono text-xs text-ink-2">{s.id}</div>
              <p className="mt-1 whitespace-pre-wrap rounded-xl bg-canvas px-3 py-2 text-sm text-ink">{s.text}</p>
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
          <h1 className="text-2xl font-semibold tracking-tight text-ink">Prompts</h1>
          <p className="text-sm text-ink-2">
            The system prompt of each harness, sent in this order. <code>{'{cwd}'}</code> becomes the working directory.
          </p>
        </div>
      </div>

      <div className="mt-6 space-y-4">
        {isLoading && <div className="h-32 animate-pulse rounded-card bg-line-soft" />}
        {error && <p className="text-sm text-bad">Could not load your harnesses.</p>}
        {!isLoading && !error && harnesses.length === 0 && (
          <div className="rounded-card border border-dashed border-line bg-surface p-8 text-center text-sm text-ink-2">
            No harnesses yet.{' '}
            <Link href="/harness/new" className="font-medium text-accent-600 hover:text-accent-700">Create one</Link>{' '}
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
