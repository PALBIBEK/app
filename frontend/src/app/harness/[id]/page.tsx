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
import { ArrowLeft, SpinnerGap, Trash } from '@phosphor-icons/react';

import { HarnessBuilder } from '@/components/harness/builder/HarnessBuilder';
import { AiPlatformBanner } from '@/components/platform/AiPlatformBanner';
import { useDeleteHarness, useHarness } from '@/lib/api/hooks/useHarnesses';
import { AppShell, PageBody } from '@/components/layout/AppShell';
import { Button, Card, ErrorNote, LinkButton, PageHeader } from '@/components/ui';

function HarnessPageContent(): JSX.Element {
  const params = useParams();
  const router = useRouter();
  const id = typeof params.id === 'string' ? params.id : '';
  const { data: harness, isLoading, error } = useHarness(id);
  const deleteHarness = useDeleteHarness();

  if (isLoading) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center gap-3 text-ink-2">
        <SpinnerGap size={20} className="animate-spin" />
        Loading the agent…
      </div>
    );
  }

  if (error || !harness) {
    return (
      <PageBody>
        <ErrorNote message="This agent could not be loaded. It may have been deleted, or belong to someone else." />
        <Link href="/" className="text-sm font-medium text-accent-700">
          Back to agents
        </Link>
      </PageBody>
    );
  }

  async function handleDelete(): Promise<void> {
    if (!window.confirm(`Delete agent “${harness!.name}”? This cannot be undone.`)) return;
    await deleteHarness.mutateAsync(harness!.id);
    router.push('/');
  }

  return (
    <PageBody>
      <PageHeader
        eyebrow={
          <Link href="/" className="inline-flex items-center gap-1 hover:text-ink">
            <ArrowLeft size={12} /> Agents
          </Link>
        }
        title={harness.name}
        description={harness.description || 'Edit the draft on its tabs, check it, then publish it as a new version.'}
        actions={
          <LinkButton href={`/harness/${harness.id}/sessions`}>Sessions</LinkButton>
        }
      />
      <AiPlatformBanner />

      <HarnessBuilder harness={harness} />

      <Card className="flex flex-wrap items-center justify-between gap-4 px-5 py-4">
        <div>
          <p className="text-sm font-semibold">Delete agent</p>
          <p className="mt-0.5 text-[13px] text-ink-2">Its published versions stay in the catalog for sessions already running on them.</p>
        </div>
        <Button variant="danger" onClick={() => void handleDelete()} disabled={deleteHarness.isPending}>
          <Trash size={16} />
          {deleteHarness.isPending ? 'Deleting…' : 'Delete'}
        </Button>
      </Card>
    </PageBody>
  );
}

export default function HarnessPage(): JSX.Element {
  return (
    <AppShell>
      <HarnessPageContent />
    </AppShell>
  );
}
