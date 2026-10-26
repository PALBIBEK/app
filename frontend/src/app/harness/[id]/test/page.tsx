/**
 * Test a harness: a new session on its live version, with its workspace and tools at hand.
 */

'use client';

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { ArrowLeft, CircleNotch, ClockCounterClockwise, Wrench } from '@phosphor-icons/react';
import { toast } from 'sonner';

import { AppShell } from '@/components/layout/AppShell';
import { Card, LinkButton } from '@/components/ui';
import { AiPlatformBanner } from '@/components/platform/AiPlatformBanner';
import { SessionChat } from '@/components/session/SessionChat';
import { ConversationFiles } from '@/components/session/ConversationFiles';
import { getApiErrorMessage } from '@/lib/api/errors';
import { startupMessage, waitUntilStarted, type SessionStartup } from '@/lib/session/startup';
import { WorkspacePanel } from '@/components/session/WorkspacePanel';
import { sessionsAPI } from '@/lib/api/endpoints/sessions';
import { workspaceAPI } from '@/lib/api/endpoints/workspace';
import { useHarness } from '@/lib/api/hooks/useHarnesses';
import { useReleases } from '@/lib/api/hooks/useManifest';
import { usePlatformStatus } from '@/lib/api/hooks/usePlatformStatus';
import type { Session } from '@/lib/api/types/session.types';

export default function TestHarnessPage(): JSX.Element {
  const params = useParams();
  const harnessId = typeof params.id === 'string' ? params.id : '';
  const { data: harness } = useHarness(harnessId);
  const { data: releases } = useReleases(harnessId);
  const { data: platform, isLoading: platformLoading, isFetching } = usePlatformStatus();
  const [session, setSession] = useState<Session | null>(null);
  const [scopeId, setScopeId] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [invoking, setInvoking] = useState<string | null>(null);
  const [invokeResult, setInvokeResult] = useState<string | null>(null);
  const [filesTick, setFilesTick] = useState(0);
  const [starting, setStarting] = useState<SessionStartup | null>(null);
  const created = useRef<string | null>(null);
  const mounted = useRef(true);

  const live = releases?.[0];
  const isBound = harness?.bind?.bind_status === 'bound';
  const platformReady = !platformLoading && !isFetching && platform?.run_allowed === true;
  const canRun = Boolean(isBound && platformReady);
  const blockedReason = platformLoading || isFetching ? 'Checking platform readiness…' : !isBound ? 'Publish the harness before testing it.' : 'The AI platform is not ready.';

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  // Create the session once the page may run. Once started, a creation always finishes:
  // the gate flickers while platform status refetches, and React runs effects twice in
  // development; a cancelled first attempt used to leave the page waiting for good.
  useEffect(() => {
    if (!canRun || !harnessId || created.current === harnessId) return;
    created.current = harnessId;
    const scope = `ws-${harnessId.slice(0, 8)}-${crypto.randomUUID().slice(0, 8)}`;
    void (async () => {
      try {
        await workspaceAPI.ensureScope(scope);
        const s = await sessionsAPI.create({ harness_id: harnessId, scope_id: scope });
        if (!mounted.current) return;
        setScopeId(scope);
        if (s.status === 'starting') {
          // Created at once; the agent worker sets it up. Watch until it is ready (or why not).
          const outcome = await waitUntilStarted(() => sessionsAPI.startup(s.id), (p) => mounted.current && setStarting(p), {
            shouldStop: () => !mounted.current,
          });
          if (!mounted.current) return;
          if (!outcome.ok) throw new Error(outcome.reason);
        }
        setSession({ ...s, status: 'active' });
      } catch (err) {
        created.current = null;
        if (mounted.current) setError(getApiErrorMessage(err, 'Failed to create a session'));
      }
    })();
  }, [canRun, harnessId]);

  async function invoke(name: string): Promise<void> {
    if (!session) return;
    setInvoking(name);
    setInvokeResult(null);
    try {
      const r = await sessionsAPI.invokeTool(session.id, { name, arguments: {} });
      setInvokeResult(`${name}: ${r.ok ? JSON.stringify(r.payload ?? { ok: true }) : r.error || r.code || 'failed'}`);
      setFilesTick((t) => t + 1);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Invoke failed');
    } finally {
      setInvoking(null);
    }
  }

  return (
    <AppShell fullHeight mainClassName="flex min-h-0 flex-col overflow-hidden">
      <div className="shrink-0 px-4 pt-4 empty:hidden sm:px-6">
        <AiPlatformBanner />
      </div>

      {live?.manifest.tools.length && session ? (
        <div data-testid="harness-test-tools-invoke" className="mx-4 mt-4 flex shrink-0 flex-wrap items-center gap-2 rounded-card border border-line bg-surface px-4 py-2.5 text-[13px] sm:mx-6">
          <Wrench size={14} className="text-ink-2" aria-hidden="true" />
          <span className="text-ink-2">Run a tool directly:</span>
          {live.manifest.tools.map((name) => (
            <button
              key={name}
              type="button"
              disabled={invoking !== null}
              onClick={() => void invoke(name)}
              className="rounded-md border border-line-soft bg-canvas px-2 py-0.5 font-mono text-xs text-ink hover:border-line-strong disabled:opacity-50"
            >
              {invoking === name ? `${name}…` : name}
            </button>
          ))}
          {invokeResult ? <span className="ml-2 max-w-md truncate font-mono text-xs text-ink-2">{invokeResult}</span> : null}
        </div>
      ) : null}

      {session ? (
        <SessionChat
          session={session}
          agentName={harness?.name}
          canRun={canRun}
          blockedReason={blockedReason}
          maxSteps={live?.manifest.policy.max_steps}
          maxCost={live?.manifest.policy.max_cost_per_session || undefined}
          onApprovalTimeout={live?.manifest.policy.on_approval_timeout}
          onRunEnd={() => setFilesTick((t) => t + 1)}
          heading={{
            crumb: (
              <>
                <Link href={`/harness/${harnessId}`} className="inline-flex items-center gap-1 hover:text-ink">
                  <ArrowLeft size={12} /> {harness?.name ?? 'Agent'}
                </Link>
                <span aria-hidden="true">/</span>
                <span>Test</span>
              </>
            ),
            title: `Test ${harness?.name ?? 'agent'}`,
            meta: (
              <>
                <span className="font-mono text-[12.5px] text-ink">{live ? `v${live.version} · ${live.manifest.model}` : 'Not published'}</span>
                <Link href={`/harness/${harnessId}/sessions`} className="inline-flex items-center gap-1 hover:text-ink">
                  <ClockCounterClockwise size={13} /> History
                </Link>
              </>
            ),
          }}
          railExtra={
            scopeId ? (
              <>
                <Card>
                  <ConversationFiles
                    sessionId={session.id}
                    scopeId={scopeId}
                    disabled={!canRun}
                    refreshKey={filesTick}
                    onWorkspaceChanged={() => setFilesTick((t) => t + 1)}
                    className="px-[18px] py-4 text-sm"
                  />
                </Card>
                <Card>
                  <WorkspacePanel scopeId={scopeId} sessionId={session.id} disabled={!canRun} refreshKey={filesTick} className="px-[18px] py-4 text-sm" />
                </Card>
              </>
            ) : null
          }
        />
      ) : (
        <div className="flex flex-1 items-center justify-center gap-2 p-6 text-sm text-ink-2">
          {error ? (
            <Card className="flex max-w-2xl flex-col items-center gap-3 px-6 py-8 text-center">
              <span className="h-2.5 w-2.5 rotate-45 bg-bad" aria-hidden="true" />
              <span data-testid="session-error" className="text-ink">
                {error}
              </span>
              <LinkButton href={`/harness/${harnessId}`} size="sm">
                Open the builder
              </LinkButton>
            </Card>
          ) : canRun ? (
            <>
              <CircleNotch size={16} className="animate-spin text-accent-600" />
              <span data-testid="session-starting">{starting ? startupMessage(starting) : 'Creating session…'}</span>
            </>
          ) : (
            <span>{blockedReason}</span>
          )}
        </div>
      )}
    </AppShell>
  );
}
