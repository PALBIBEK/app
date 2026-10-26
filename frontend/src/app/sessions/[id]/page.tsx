/**
 * One session: its conversation, live, and continued from here (or forked, once ended);
 * beside it, its sandboxes, what it has used, and its files.
 */

'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { CircleNotch } from '@phosphor-icons/react';

import { AppShell } from '@/components/layout/AppShell';
import { ConversationFiles } from '@/components/session/ConversationFiles';
import { SessionChat } from '@/components/session/SessionChat';
import { WorkspacePanel } from '@/components/session/WorkspacePanel';
import { Card, ErrorNote, LinkButton, Mono, shortHash } from '@/components/ui';
import { sessionsAPI } from '@/lib/api/endpoints/sessions';
import { getApiErrorMessage } from '@/lib/api/errors';
import { useHarness } from '@/lib/api/hooks/useHarnesses';
import { useReleases } from '@/lib/api/hooks/useManifest';
import { usePlatformStatus } from '@/lib/api/hooks/usePlatformStatus';
import { useSession } from '@/lib/api/hooks/useSessions';
import { startupMessage, waitUntilStarted, type SessionStartup } from '@/lib/session/startup';
import { formatDuration, formatStamp, versionLabel } from '@/lib/utils';

/** A session that is still starting: wait for it (or show why it could not start). */
function StartingSession({ sessionId, onReady }: { sessionId: string; onReady: () => void }): JSX.Element {
  const [progress, setProgress] = useState<SessionStartup | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  useEffect(() => {
    let stop = false;
    void waitUntilStarted(() => sessionsAPI.startup(sessionId), (p) => !stop && setProgress(p), { shouldStop: () => stop })
      .then((out) => {
        if (stop) return;
        if (out.ok) onReady();
        else setFailed(out.reason);
      })
      .catch((err) => !stop && setFailed(getApiErrorMessage(err, 'Could not check the session')));
    return () => {
      stop = true;
    };
  }, [sessionId, onReady]);
  return (
    <div className="flex flex-1 items-center justify-center p-6">
      {failed ? (
        <Card className="flex max-w-xl flex-col items-center gap-3 px-6 py-8 text-center">
          <span className="h-2.5 w-2.5 rotate-45 bg-bad" aria-hidden="true" />
          <p className="font-medium">This session could not start</p>
          <p data-testid="session-error" className="text-sm text-ink-2">
            {failed}
          </p>
          <LinkButton href="/sessions" size="sm">
            Back to sessions
          </LinkButton>
        </Card>
      ) : (
        <div className="flex items-center gap-2 text-sm text-ink-2">
          <CircleNotch size={16} className="animate-spin text-accent-600" />
          <span data-testid="session-starting">{startupMessage(progress)}</span>
        </div>
      )}
    </div>
  );
}

function Elapsed({ from }: { from: string }): JSX.Element {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);
  return <span className="tabular">{formatDuration(now - new Date(from).getTime())}</span>;
}

function SessionPageContent(): JSX.Element {
  const params = useParams();
  const sessionId = typeof params.id === 'string' ? params.id : '';
  const { data: session, isLoading, error, refetch } = useSession(sessionId);
  const onReady = useCallback(() => void refetch(), [refetch]);
  const { data: harness } = useHarness(session?.harness_id ?? '');
  const { data: releases } = useReleases(session?.harness_id);
  const { data: platform } = usePlatformStatus();
  const [filesTick, setFilesTick] = useState(0);
  const bump = useCallback(() => setFilesTick((t) => t + 1), []);

  if (isLoading) {
    return (
      <div className="flex flex-1 items-center justify-center gap-2 text-sm text-ink-2">
        <CircleNotch size={16} className="animate-spin" /> Loading the session…
      </div>
    );
  }
  if (error || !session) {
    return (
      <div className="mx-auto mt-16 w-full max-w-md px-4">
        <ErrorNote message="This session could not be loaded. It may have been deleted, or belong to someone else." />
        <Link href="/sessions" className="mt-3 inline-block text-sm font-medium text-accent-700">
          Back to sessions
        </Link>
      </div>
    );
  }

  if (session.status === 'starting') return <StartingSession sessionId={session.id} onReady={onReady} />;
  if (session.status === 'failed' && !session.last_seq) {
    return <StartingSession sessionId={session.id} onReady={onReady} />; // shows why it failed
  }

  // The version this session runs (a session keeps its version when the harness is republished).
  const release = releases?.find((r) => r.composition_digest === session.composition_digest);
  const ended = ['terminated', 'failed', 'budget_exceeded'].includes(session.status);
  const agent = harness?.name ?? 'agent';
  const version = release ? `v${release.version}` : versionLabel(session.harness_version);

  return (
    <SessionChat
      session={session}
      agentName={harness?.name}
      canRun={platform?.run_allowed !== false}
      blockedReason="The AI platform is not ready."
      maxSteps={release?.manifest.policy.max_steps}
      maxCost={release?.manifest.policy.max_cost_per_session || undefined}
      onApprovalTimeout={release?.manifest.policy.on_approval_timeout}
      onRunEnd={bump}
      heading={{
        crumb: (
          <>
            <Link href="/sessions" className="hover:text-ink">
              Sessions
            </Link>
            <span aria-hidden="true">/</span>
            <Mono className="text-[12.5px]">{session.id.slice(0, 13)}</Mono>
          </>
        ),
        title: session.title || (harness ? `${harness.name} session` : 'Session'),
        meta: (
          <>
            {harness ? (
              <Link href={`/harness/${harness.id}`} className="font-mono text-[12.5px] text-ink hover:text-accent-700">
                {agent}@{version}
              </Link>
            ) : (
              <Mono>{version}</Mono>
            )}
            {session.composition_digest ? (
              <Mono copy={session.composition_digest} title={session.composition_digest}>
                sha256:{shortHash(session.composition_digest)}
              </Mono>
            ) : null}
            <span>
              Started {formatStamp(session.created_at)}
              {!ended ? (
                <>
                  {' · '}
                  <Elapsed from={session.created_at} />
                </>
              ) : null}
            </span>
          </>
        ),
      }}
      railExtra={
        session.scope_id ? (
          <>
            <Card>
              <ConversationFiles
                sessionId={session.id}
                scopeId={session.scope_id}
                refreshKey={filesTick}
                onWorkspaceChanged={bump}
                className="px-[18px] py-4 text-sm"
              />
            </Card>
            <Card>
              <WorkspacePanel scopeId={session.scope_id} sessionId={session.id} refreshKey={filesTick} className="px-[18px] py-4 text-sm" />
            </Card>
          </>
        ) : null
      }
    />
  );
}

export default function SessionPage(): JSX.Element {
  return (
    <AppShell fullHeight mainClassName="flex min-h-0 flex-col overflow-hidden">
      <SessionPageContent />
    </AppShell>
  );
}
