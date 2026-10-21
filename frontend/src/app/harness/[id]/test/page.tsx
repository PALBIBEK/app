/**
 * Test a harness: a new session on its live version, with its workspace and tools at hand.
 */

'use client';

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { ArrowLeft, CircleNotch, ClockCounterClockwise, Robot, Wrench } from '@phosphor-icons/react';
import { toast } from 'sonner';

import { AuthGuard } from '@/components/auth/AuthGuard';
import { AiPlatformBanner } from '@/components/platform/AiPlatformBanner';
import { SessionChat } from '@/components/session/SessionChat';
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
        setSession(s);
      } catch (err) {
        created.current = null;
        if (mounted.current) setError(err instanceof Error ? err.message : 'Failed to create a session');
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
    <AuthGuard>
      <div className="flex h-[100dvh] flex-col bg-gradient-subtle">
        <header className="flex h-16 shrink-0 items-center justify-between border-b border-slate-200/80 bg-white/90 px-6 backdrop-blur-md">
          <div className="flex items-center gap-5">
            <Link href={`/harness/${harnessId}`} className="group flex items-center gap-2 text-sm font-medium text-slate-500 hover:text-slate-700">
              <ArrowLeft size={16} className="transition-transform group-hover:-translate-x-1" /> Back
            </Link>
            <div className="h-6 w-px bg-slate-200" />
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-primary-500 to-primary-600 shadow-lg shadow-primary-500/25">
                <Robot size={20} weight="fill" className="text-white" />
              </div>
              <div>
                <p className="text-sm font-semibold text-slate-900">{harness?.name ?? 'Harness'}</p>
                <p className="text-xs text-slate-500">
                  {live ? `Version ${live.version} · ${live.manifest.model}` : 'Not published'}
                  {session ? ` · session ${session.id.slice(0, 8)}` : ''}
                </p>
              </div>
            </div>
          </div>
          <Link href={`/harness/${harnessId}/sessions`} className="flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-100">
            <ClockCounterClockwise size={16} /> History
          </Link>
        </header>

        <div className="shrink-0 border-b border-slate-200/80 bg-white/70 px-6 py-2">
          <AiPlatformBanner />
        </div>

        {scopeId ? <WorkspacePanel scopeId={scopeId} sessionId={session?.id} disabled={!canRun} refreshKey={filesTick} /> : null}

        {live?.manifest.tools.length ? (
          <div data-testid="harness-test-tools-invoke" className="flex shrink-0 flex-wrap items-center gap-2 border-b border-slate-200/80 bg-white/70 px-6 py-2 text-xs">
            <Wrench size={14} className="text-slate-500" />
            <span className="text-slate-500">Run a tool directly:</span>
            {live.manifest.tools.map((name) => (
              <button key={name} type="button" disabled={!session || invoking !== null} onClick={() => void invoke(name)} className="rounded-lg border border-primary-200 bg-primary-50 px-2 py-0.5 font-mono text-primary-800 disabled:opacity-50">
                {invoking === name ? `${name}…` : name}
              </button>
            ))}
            {invokeResult ? <span className="ml-2 max-w-md truncate font-mono text-slate-600">{invokeResult}</span> : null}
          </div>
        ) : null}

        {session ? (
          <SessionChat
            session={session}
            canRun={canRun}
            blockedReason={blockedReason}
            maxSteps={live?.manifest.policy.max_steps}
            onApprovalTimeout={live?.manifest.policy.on_approval_timeout}
            onRunEnd={() => setFilesTick((t) => t + 1)}
          />
        ) : (
          <div className="flex flex-1 items-center justify-center gap-2 text-sm text-slate-500">
            {error ? (
              <span className="text-red-600">{error}</span>
            ) : canRun ? (
              <>
                <CircleNotch size={16} className="animate-spin text-primary-500" /> Creating session…
              </>
            ) : (
              <span className="text-amber-700">{blockedReason}</span>
            )}
          </div>
        )}
      </div>
    </AuthGuard>
  );
}
