/**
 * One session: its conversation, live, and continued from here (or forked, once ended).
 */

'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { ArrowLeft, CircleNotch, WarningCircle } from '@phosphor-icons/react';

import { AuthGuard } from '@/components/auth/AuthGuard';
import { Header } from '@/components/layout/Header';
import { Sidebar } from '@/components/layout/Sidebar';
import { SessionChat } from '@/components/session/SessionChat';
import { ConversationFiles } from '@/components/session/ConversationFiles';
import { WorkspacePanel } from '@/components/session/WorkspacePanel';
import { useHarness } from '@/lib/api/hooks/useHarnesses';
import { useReleases } from '@/lib/api/hooks/useManifest';
import { usePlatformStatus } from '@/lib/api/hooks/usePlatformStatus';
import { useSession } from '@/lib/api/hooks/useSessions';

function SessionPageContent(): JSX.Element {
  const params = useParams();
  const sessionId = typeof params.id === 'string' ? params.id : '';
  const { data: session, isLoading, error } = useSession(sessionId);
  const { data: harness } = useHarness(session?.harness_id ?? '');
  const { data: releases } = useReleases(session?.harness_id);
  const { data: platform } = usePlatformStatus();
  const [filesTick, setFilesTick] = useState(0);

  if (isLoading) {
    return (
      <div className="flex flex-1 items-center justify-center gap-2 text-sm text-slate-500">
        <CircleNotch size={16} className="animate-spin" /> Loading the session…
      </div>
    );
  }
  if (error || !session) {
    return (
      <div className="mx-auto mt-16 max-w-md rounded-2xl border border-red-200 bg-red-50 p-6 text-center text-sm text-red-700">
        <WarningCircle size={28} className="mx-auto text-red-500" />
        <p className="mt-2">This session could not be loaded.</p>
        <Link href="/sessions" className="mt-3 inline-block font-medium text-primary-600">Back to sessions</Link>
      </div>
    );
  }

  // The version this session runs (a session keeps its version when the harness is republished).
  const release = releases?.find((r) => r.composition_digest === session.composition_digest);
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 flex-wrap items-center gap-3 border-b border-slate-200/80 bg-white/80 px-6 py-3">
        <Link href="/sessions" className="group flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-700">
          <ArrowLeft size={14} className="transition-transform group-hover:-translate-x-1" /> Sessions
        </Link>
        <div className="h-5 w-px bg-slate-200" />
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-slate-900">
            {session.title || (harness ? <Link href={`/harness/${harness.id}`} className="hover:text-primary-700">{harness.name}</Link> : 'Session')}
          </p>
          <p className="truncate text-xs text-slate-500">
            {release ? `Version ${release.version} · ` : ''}
            {session.harness_version} · <span className="font-mono">{session.id.slice(0, 8)}</span>
          </p>
        </div>
      </div>
      {session.scope_id ? <WorkspacePanel scopeId={session.scope_id} sessionId={session.id} refreshKey={filesTick} /> : null}
      {session.scope_id ? (
        <ConversationFiles sessionId={session.id} scopeId={session.scope_id} refreshKey={filesTick} onWorkspaceChanged={() => setFilesTick((t) => t + 1)} />
      ) : null}
      <SessionChat
        session={session}
        canRun={platform?.run_allowed !== false}
        blockedReason="The AI platform is not ready."
        maxSteps={release?.manifest.policy.max_steps}
        onApprovalTimeout={release?.manifest.policy.on_approval_timeout}
        onRunEnd={() => setFilesTick((t) => t + 1)}
      />
    </div>
  );
}

export default function SessionPage(): JSX.Element {
  return (
    <AuthGuard>
      <div className="grid h-[100dvh] grid-cols-[auto_1fr] grid-rows-[auto_1fr] bg-gradient-subtle">
        <div className="col-span-2">
          <Header />
        </div>
        <Sidebar />
        <main className="flex min-h-0 flex-col overflow-hidden">
          <SessionPageContent />
        </main>
      </div>
    </AuthGuard>
  );
}
