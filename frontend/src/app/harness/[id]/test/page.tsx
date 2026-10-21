/**
 * Test Harness (Chat Interface) — live BFF sessions + SSE (D4).
 */

'use client';

import { useState, useRef, useEffect, useCallback } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { motion, AnimatePresence } from 'framer-motion';
import {
  ArrowLeft,
  PaperPlaneTilt,
  Robot,
  User,
  Gear,
  Stop,
  ClockCounterClockwise,
  Code,
  CheckCircle,
  CircleNotch,
  Warning,
  Sparkle,
  Copy,
  Check,
  UploadSimple,
  ShieldWarning,
  GitBranch,
  Wrench,
} from '@phosphor-icons/react';
import { AuthGuard } from '@/components/auth/AuthGuard';
import { AiPlatformBanner } from '@/components/platform/AiPlatformBanner';
import { usePlatformStatus } from '@/lib/api/hooks/usePlatformStatus';
import { useHarness } from '@/lib/api/hooks/useHarnesses';
import { useReleases } from '@/lib/api/hooks/useManifest';
import { sessionsAPI } from '@/lib/api/endpoints/sessions';
import { workspaceAPI, type WorkspaceFile } from '@/lib/api/endpoints/workspace';
import type { ChatMessage, Session, ToolCallView } from '@/lib/api/types/session.types';
import {
  applyWireEvent,
  createSessionViewState,
  displaySubagentProvider,
  type SessionViewState,
  type SubagentChipView,
} from '@/lib/session/wireEventReducer';
import { cn } from '@/lib/utils';
import { toast } from 'sonner';

const messageVariants = {
  hidden: { opacity: 0, y: 20, scale: 0.95 },
  visible: {
    opacity: 1,
    y: 0,
    scale: 1,
    transition: { duration: 0.3, ease: [0.25, 0.46, 0.45, 0.94] },
  },
  exit: { opacity: 0, scale: 0.95, transition: { duration: 0.2 } },
};

const toolCallVariants = {
  hidden: { opacity: 0, x: -10 },
  visible: { opacity: 1, x: 0, transition: { duration: 0.2 } },
};

export default function TestHarnessPage(): JSX.Element {
  const params = useParams();
  const [input, setInput] = useState('');
  const [session, setSession] = useState<Session | null>(null);
  const [viewState, setViewState] = useState<SessionViewState>(() => createSessionViewState());
  const [isBusy, setIsBusy] = useState(false);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [scopeId, setScopeId] = useState<string>('');
  const [workspaceFiles, setWorkspaceFiles] = useState<WorkspaceFile[]>([]);
  const [uploading, setUploading] = useState(false);
  const [invokingTool, setInvokingTool] = useState<string | null>(null);
  const [invokeResult, setInvokeResult] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const createdForHarness = useRef<string | null>(null);
  const mountedRef = useRef(true);

  const { data: platform, isLoading: platformLoading, isFetching } = usePlatformStatus();
  const harnessId = typeof params.id === 'string' ? params.id : '';
  const { data: harness } = useHarness(harnessId);
  const { data: releases } = useReleases(harnessId);
  const isBound = harness?.bind?.bind_status === 'bound';
  // Fail-closed while platform status is still loading/fetching
  const platformReady = !platformLoading && !isFetching && platform?.run_allowed === true;
  const runAllowed = Boolean(isBound && platformReady);

  const messages = viewState.messages;
  const pendingTools = Array.from(viewState.pendingTools.values());
  const pendingSubagents = Array.from(viewState.pendingSubagents.values());
  const pendingApprovals = viewState.pendingApprovals;
  const streamError = viewState.error || sessionError;
  const awaitingApproval =
    viewState.awaitingApproval ||
    pendingApprovals.length > 0 ||
    session?.status === 'awaiting_approval';
  const isGenerating =
    isBusy ||
    pendingTools.some((t) => t.status === 'running') ||
    pendingSubagents.some((s) => s.status === 'running');
  const sendBlocked = !runAllowed || !session || awaitingApproval || isGenerating;

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, pendingTools.length, pendingSubagents.length, isGenerating]);

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height =
        Math.min(textareaRef.current.scrollHeight, 150) + 'px';
    }
  }, [input]);

  // Create the session once the gate opens. Once started, a creation always finishes:
  // the gate can flicker (platform status refetches) and React runs effects twice in
  // development, and cancelling a started creation while the "created" marker stayed
  // set left the page on "Creating session…" for good.
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    if (!runAllowed || !harnessId) return;
    if (createdForHarness.current === harnessId) return;
    createdForHarness.current = harnessId;
    setCreating(true);
    setSessionError(null);

    const nextScope =
      typeof crypto !== 'undefined' && 'randomUUID' in crypto
        ? `ws-${harnessId.slice(0, 8)}-${crypto.randomUUID().slice(0, 8)}`
        : `ws-${harnessId.slice(0, 8)}-${Date.now().toString(36)}`;

    void (async () => {
      try {
        await workspaceAPI.ensureScope(nextScope);
        const created = await sessionsAPI.create({ harness_id: harnessId, scope_id: nextScope });
        if (!mountedRef.current) return;
        setScopeId(nextScope);
        setSession(created);
        setViewState(createSessionViewState());
        const listed = await workspaceAPI.listFiles(nextScope);
        if (mountedRef.current) setWorkspaceFiles(listed);
      } catch (err) {
        createdForHarness.current = null;
        if (!mountedRef.current) return;
        const message = err instanceof Error ? err.message : 'Failed to create session';
        setSessionError(message);
        toast.error(message);
      } finally {
        if (mountedRef.current) setCreating(false);
      }
    })();
  }, [runAllowed, harnessId]);

  const refreshWorkspaceFiles = useCallback(async (sid: string) => {
    const listed = await workspaceAPI.listFiles(sid);
    setWorkspaceFiles(listed);
  }, []);

  const handleWorkspaceUpload = useCallback(
    async (fileList: FileList | null) => {
      if (!fileList?.length || !scopeId) {
        toast.error('Agent workspace scope is not ready yet');
        return;
      }
      setUploading(true);
      try {
        for (const file of Array.from(fileList)) {
          const path = file.name.replace(/^\/+/, '');
          await workspaceAPI.uploadFile(scopeId, path, file, file.type || undefined);
        }
        await refreshWorkspaceFiles(scopeId);
        if (session?.id) {
          await workspaceAPI.rehydrateSession(session.id);
        }
        toast.success('Uploaded to agent /workspace (durable)');
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Upload failed';
        toast.error(message);
      } finally {
        setUploading(false);
        if (fileInputRef.current) fileInputRef.current.value = '';
      }
    },
    [scopeId, session?.id, refreshWorkspaceFiles]
  );

  // The tools of the version this session runs: the live release's manifest.
  const attachedTools = (releases?.[0]?.manifest.tools ?? []).map((name) => ({ id: name, name }));

  const handleInvokeTool = useCallback(
    async (name: string, args: Record<string, unknown> = {}) => {
      if (!session?.id) {
        toast.error('Session not ready');
        return;
      }
      setInvokingTool(name);
      setInvokeResult(null);
      try {
        const result = await sessionsAPI.invokeTool(session.id, {
          name,
          arguments: args,
        });
        const summary = result.ok
          ? JSON.stringify(result.payload ?? { ok: true })
          : result.error || result.code || 'invoke failed';
        setInvokeResult(`${name}: ${summary}`);
        if (result.ok) {
          toast.success(`Invoked ${name}`);
        } else {
          toast.error(result.error || result.code || `Invoke ${name} failed`);
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Invoke failed';
        setInvokeResult(`${name}: ${message}`);
        toast.error(message);
      } finally {
        setInvokingTool(null);
      }
    },
    [session?.id]
  );

  // Open SSE when session exists
  useEffect(() => {
    if (!session?.id) return;

    const ac = new AbortController();
    let cancelled = false;

    (async () => {
      try {
        for await (const event of sessionsAPI.openEventStream(session.id, {
          signal: ac.signal,
        })) {
          if (cancelled) break;
          setViewState((prev) => applyWireEvent(prev, event));
          if (event.type === 'settled' || event.type === 'run_end') {
            setIsBusy(false);
          }
          if (event.type === 'error') {
            setIsBusy(false);
          }
        }
      } catch (err) {
        if (cancelled || (err instanceof DOMException && err.name === 'AbortError')) return;
        const message = err instanceof Error ? err.message : 'Event stream failed';
        setSessionError(message);
        setIsBusy(false);
      }
    })();

    return () => {
      cancelled = true;
      ac.abort();
    };
  }, [session?.id]);

  // Poll BFF approvals when session may be awaiting (SSE + recovery).
  useEffect(() => {
    if (!session?.id) return;
    let cancelled = false;

    const refresh = async () => {
      try {
        const rows = await sessionsAPI.listApprovals(session.id);
        if (cancelled) return;
        if (!rows.length) {
          setViewState((prev) => ({
            ...prev,
            pendingApprovals: [],
            awaitingApproval: false,
          }));
          setSession((s) => (s ? { ...s, status: s.status === 'awaiting_approval' ? 'active' : s.status } : s));
          return;
        }
        setViewState((prev) => ({
          ...prev,
          pendingApprovals: rows.map((r) => ({
            approval_id: r.approval_id,
            prompt: r.prompt,
            options: r.options?.length ? r.options : ['approve', 'deny'],
            timeout_s: r.timeout_s,
          })),
          awaitingApproval: true,
        }));
        setSession((s) => (s ? { ...s, status: 'awaiting_approval' } : s));
      } catch {
        /* keep SSE-driven state */
      }
    };

    void refresh();
    const timer = window.setInterval(() => {
      if (awaitingApproval || session.status === 'awaiting_approval') {
        void refresh();
      }
    }, 4000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [session?.id, session?.status, awaitingApproval]);

  const handleApprovalRespond = useCallback(
    async (approvalId: string, choice: string) => {
      if (!session?.id) return;
      try {
        await sessionsAPI.respondApproval(session.id, approvalId, choice);
        setViewState((prev) => ({
          ...prev,
          pendingApprovals: prev.pendingApprovals.filter((a) => a.approval_id !== approvalId),
          awaitingApproval:
            prev.pendingApprovals.filter((a) => a.approval_id !== approvalId).length > 0,
        }));
        const remaining = await sessionsAPI.listApprovals(session.id);
        if (!remaining.length) {
          setSession((s) => (s ? { ...s, status: 'active' } : s));
        }
        toast.success(choice.toLowerCase().includes('deny') ? 'Denied' : 'Approved');
      } catch (err) {
        toast.error(err instanceof Error ? err.message : 'Failed to respond');
      }
    },
    [session?.id]
  );

  const handleAbort = useCallback(async () => {
    if (!session?.id) {
      setIsBusy(false);
      return;
    }
    try {
      await sessionsAPI.abort(session.id);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to abort');
    } finally {
      setIsBusy(false);
    }
  }, [session?.id]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const text = input.trim();
    if (!text || sendBlocked || !session?.id) return;

    const localMessage: ChatMessage = {
      id: `local-${Date.now()}`,
      role: 'user',
      content: text,
      timestamp: new Date().toISOString(),
    };

    setViewState((prev) => ({
      ...prev,
      messages: [...prev.messages, localMessage],
      settled: false,
      error: null,
    }));
    setInput('');
    setIsBusy(true);
    setSessionError(null);

    try {
      await sessionsAPI.sendMessage(session.id, text);
    } catch (err) {
      setIsBusy(false);
      const message = err instanceof Error ? err.message : 'Failed to send message';
      setSessionError(message);
      toast.error(message);
    }
  };

  return (
    <AuthGuard>
      <div className="flex min-h-[100dvh] flex-col bg-gradient-subtle">
        <motion.header
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          className="h-16 border-b border-slate-200/80 bg-white/90 backdrop-blur-md"
        >
          <div className="flex h-full items-center justify-between px-6">
            <div className="flex items-center gap-5">
              <Link
                href={`/harness/${params.id}`}
                className="group flex items-center gap-2 text-sm font-medium text-slate-500 transition-colors hover:text-slate-700"
              >
                <ArrowLeft size={16} className="transition-transform group-hover:-translate-x-1" />
                Back
              </Link>
              <div className="h-6 w-px bg-slate-200" />
              <div className="flex items-center gap-3">
                <motion.div
                  whileHover={{ scale: 1.05, rotate: 5 }}
                  className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-primary-500 to-primary-600 shadow-lg shadow-primary-500/25"
                >
                  <Robot size={20} weight="fill" className="text-white" />
                </motion.div>
                <div>
                  <p className="text-sm font-semibold text-slate-900">
                    {harness?.name ?? 'Harness'}
                  </p>
                  <p className="text-xs text-slate-500">
                    {session
                      ? `${session.status} · ${session.id.slice(0, 8)}…`
                      : harness?.bind?.harness_version ?? harness?.model ?? 'unbound'}
                  </p>
                </div>
              </div>
            </div>

            <div className="flex items-center gap-2">
              <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
                <Link
                  href={
                    session
                      ? `/subagents/spawn?session=${encodeURIComponent(session.id)}`
                      : '/subagents'
                  }
                  className={cn(
                    'flex items-center gap-2 rounded-xl px-4 py-2',
                    'text-sm font-medium text-slate-600',
                    'hover:bg-slate-100 transition-colors'
                  )}
                >
                  <GitBranch size={16} />
                  Subagents
                </Link>
              </motion.div>
              <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
                <Link
                  href={`/harness/${params.id}/sessions`}
                  className={cn(
                    'flex items-center gap-2 rounded-xl px-4 py-2',
                    'text-sm font-medium text-slate-600',
                    'hover:bg-slate-100 transition-colors'
                  )}
                >
                  <ClockCounterClockwise size={16} />
                  History
                </Link>
              </motion.div>
              <motion.button
                whileHover={{ scale: 1.02 }}
                whileTap={{ scale: 0.98 }}
                type="button"
                className={cn(
                  'flex items-center gap-2 rounded-xl px-4 py-2',
                  'text-sm font-medium text-slate-600',
                  'hover:bg-slate-100 transition-colors'
                )}
              >
                <Gear size={16} />
                Settings
              </motion.button>
            </div>
          </div>
        </motion.header>

        <div className="border-b border-slate-200/80 bg-white/70 px-6 py-3">
          <AiPlatformBanner />
        </div>

        <div className="border-b border-slate-200/80 bg-slate-50/80 px-6 py-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-sm font-semibold text-slate-800">Agent /workspace files</p>
              <p className="text-xs text-slate-500">
                Durable scope (not Settings org chrome)
                {scopeId ? ` · ${scopeId}` : ''}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <input
                ref={fileInputRef}
                type="file"
                className="hidden"
                data-testid="agent-workspace-upload"
                onChange={(e) => void handleWorkspaceUpload(e.target.files)}
              />
              <motion.button
                whileHover={{ scale: 1.02 }}
                whileTap={{ scale: 0.98 }}
                type="button"
                disabled={!scopeId || uploading || !runAllowed}
                onClick={() => fileInputRef.current?.click()}
                className={cn(
                  'inline-flex items-center gap-2 rounded-xl px-3 py-2 text-sm font-medium',
                  'bg-primary-600 text-white shadow-sm',
                  'disabled:cursor-not-allowed disabled:opacity-50'
                )}
              >
                <UploadSimple size={16} />
                {uploading ? 'Uploading…' : 'Upload to /workspace'}
              </motion.button>
            </div>
          </div>
          {workspaceFiles.length > 0 ? (
            <ul className="mt-2 flex flex-wrap gap-2" data-testid="agent-workspace-file-list">
              {workspaceFiles.map((f) => (
                <li
                  key={f.path}
                  className="rounded-lg border border-slate-200 bg-white px-2 py-1 text-xs text-slate-700"
                >
                  {f.path}
                  <span className="ml-1 text-slate-400">v{f.version}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-xs text-slate-400">No files in durable agent workspace yet.</p>
          )}
        </div>

        <div
          className="border-b border-slate-200/80 bg-white/70 px-6 py-3"
          data-testid="harness-test-tools-invoke"
        >
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-sm font-semibold text-slate-800">Session tools</p>
              <p className="text-xs text-slate-500">
                The live version’s tools: invoke one directly, outside the conversation
              </p>
            </div>
            <motion.button
              whileHover={{ scale: 1.02 }}
              whileTap={{ scale: 0.98 }}
              type="button"
              disabled={!session?.id || invokingTool !== null || !runAllowed}
              onClick={() => void handleInvokeTool('echo', { text: 'local_demo' })}
              className={cn(
                'inline-flex items-center gap-2 rounded-xl px-3 py-2 text-sm font-medium',
                'border border-slate-200 bg-white text-slate-700 shadow-sm',
                'disabled:cursor-not-allowed disabled:opacity-50'
              )}
            >
              <Wrench size={16} />
              {invokingTool === 'echo' ? 'Invoking echo…' : 'Invoke echo'}
            </motion.button>
          </div>
          {attachedTools.length > 0 ? (
            <ul className="mt-2 flex flex-wrap gap-2">
              {attachedTools.map((t) => (
                <li key={t.id}>
                  <button
                    type="button"
                    disabled={!session?.id || invokingTool !== null || !runAllowed}
                    onClick={() =>
                      void handleInvokeTool(t.name, {})
                    }
                    className="rounded-lg border border-primary-200 bg-primary-50 px-2 py-1 text-xs font-medium text-primary-800 disabled:opacity-50"
                    title={t.id}
                  >
                    {invokingTool === t.name ? `Invoking ${t.name}…` : `Invoke ${t.name}`}
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-xs text-slate-400">
              No tools in the published version: add some on the harness&apos;s Tools tab and publish.
            </p>
          )}
          {invokeResult ? (
            <p className="mt-2 truncate font-mono text-[11px] text-slate-600">{invokeResult}</p>
          ) : null}
        </div>

        {!runAllowed && (
          <div className="border-b border-amber-200 bg-amber-50 px-6 py-2 text-center text-sm text-amber-800">
            {platformLoading || isFetching
              ? 'Checking platform readiness…'
              : !isBound
                ? 'Harness must be bound before you can run.'
                : 'AI platform is not ready. Run is blocked.'}
          </div>
        )}

        {streamError && (
          <div className="border-b border-red-200 bg-red-50 px-6 py-2 text-center text-sm text-red-700">
            {streamError}
          </div>
        )}

        {awaitingApproval && pendingApprovals.length > 0 && (
          <div className="border-b border-amber-300 bg-amber-50 px-6 py-4">
            <div className="mx-auto flex max-w-3xl flex-col gap-3">
              {pendingApprovals.map((approval) => {
                const allowOpt =
                  approval.options.find((o) =>
                    /^(approve|allow|yes|y|ok)$/i.test(o)
                  ) ?? approval.options[0];
                const denyOpt =
                  approval.options.find((o) => /^(deny|reject|no|n)$/i.test(o)) ??
                  approval.options[approval.options.length - 1];
                return (
                  <div
                    key={approval.approval_id}
                    className="flex flex-col gap-3 rounded-xl border border-amber-200 bg-white p-4 shadow-sm sm:flex-row sm:items-center sm:justify-between"
                    data-testid="approval-banner"
                  >
                    <div className="flex items-start gap-3">
                      <ShieldWarning size={22} className="mt-0.5 text-amber-600" weight="fill" />
                      <div>
                        <p className="text-sm font-semibold text-slate-900">Approval required</p>
                        <p className="mt-1 text-sm text-slate-600">{approval.prompt}</p>
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      <button
                        type="button"
                        data-testid="approval-deny"
                        onClick={() => void handleApprovalRespond(approval.approval_id, denyOpt)}
                        className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
                      >
                        {denyOpt}
                      </button>
                      <button
                        type="button"
                        data-testid="approval-approve"
                        onClick={() => void handleApprovalRespond(approval.approval_id, allowOpt)}
                        className="rounded-xl bg-amber-600 px-4 py-2 text-sm font-medium text-white hover:bg-amber-700"
                      >
                        {allowOpt}
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

      <div className="flex-1 overflow-auto">
          <div className="mx-auto max-w-3xl px-6 py-8">
            {creating && messages.length === 0 && (
              <div className="flex items-center justify-center gap-2 py-20 text-sm text-slate-500">
                <CircleNotch size={16} className="animate-spin text-primary-500" />
                Creating session…
              </div>
            )}

            {!creating && messages.length === 0 && pendingTools.length === 0 && (
              <motion.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                className="flex flex-col items-center justify-center py-20 text-center"
              >
                <motion.div
                  animate={{ rotate: [0, 10, -10, 0] }}
                  transition={{ repeat: Infinity, duration: 4, ease: 'easeInOut' }}
                  className="flex h-20 w-20 items-center justify-center rounded-3xl bg-gradient-to-br from-primary-500 to-primary-600 shadow-2xl shadow-primary-500/30"
                >
                  <Sparkle size={36} weight="fill" className="text-white" />
                </motion.div>
                <h2 className="mt-6 text-xl font-semibold text-slate-900">Start a conversation</h2>
                <p className="mt-2 max-w-sm text-sm text-slate-500">
                  Test your harness by sending a message. The agent will respond using its configured
                  tools and prompt.
                </p>
              </motion.div>
            )}

            <div className="space-y-6">
              <AnimatePresence initial={false}>
                {messages.map((message) => (
                  <MessageBubble key={message.id} message={message} />
                ))}
              </AnimatePresence>

              {pendingTools.length > 0 && (
                <div className="space-y-2 pl-14">
                  {pendingTools.map((tool) => (
                    <PendingToolRow key={tool.id} tool={tool} />
                  ))}
                </div>
              )}

              {pendingSubagents.length > 0 && (
                <div className="space-y-2 pl-14">
                  {pendingSubagents.map((chip) => (
                    <SubagentChipRow key={chip.run_id} chip={chip} sessionId={session?.id} />
                  ))}
                </div>
              )}

              <AnimatePresence>
                {isGenerating && pendingTools.length === 0 && pendingSubagents.every((s) => s.status !== 'running') && (
                  <motion.div
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -10 }}
                    className="flex items-start gap-4"
                  >
                    <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-primary-500 to-primary-600 shadow-md">
                      <Robot size={18} weight="fill" className="text-white" />
                    </div>
                    <div className="flex items-center gap-3 rounded-2xl bg-white border border-slate-200 px-5 py-3 shadow-sm">
                      <motion.div
                        animate={{ rotate: 360 }}
                        transition={{ repeat: Infinity, duration: 1, ease: 'linear' }}
                      >
                        <CircleNotch size={16} className="text-primary-500" />
                      </motion.div>
                      <span className="text-sm text-slate-500">Thinking...</span>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>

              <div ref={messagesEndRef} />
            </div>
          </div>
        </div>

        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          className="border-t border-slate-200 bg-white/90 backdrop-blur-md"
        >
          <div className="mx-auto max-w-3xl px-6 py-5">
            <form onSubmit={handleSubmit}>
              <motion.div
                whileFocus={{ scale: 1.005 }}
                className={cn(
                  'flex items-end gap-3 rounded-2xl p-3',
                  'border-2 border-slate-200 bg-white',
                  'shadow-lg shadow-slate-900/5 transition-all duration-200',
                  'focus-within:border-primary-400 focus-within:shadow-xl focus-within:shadow-primary-500/10'
                )}
              >
                <textarea
                  ref={textareaRef}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault();
                      handleSubmit(e);
                    }
                  }}
                  placeholder={
                    !runAllowed
                      ? 'Run blocked until platform is ready…'
                      : !session
                        ? 'Waiting for session…'
                        : awaitingApproval
                          ? 'Waiting for approval…'
                          : 'Send a message...'
                  }
                  rows={1}
                  disabled={sendBlocked}
                  className={cn(
                    'flex-1 resize-none px-2 py-2',
                    'text-[15px] text-slate-900 placeholder:text-slate-400',
                    'bg-transparent outline-none',
                    'max-h-36 disabled:cursor-not-allowed'
                  )}
                />

                <div className="flex items-center gap-2 pb-0.5">
                  <AnimatePresence mode="wait">
                    {isGenerating ? (
                      <motion.button
                        key="stop"
                        initial={{ scale: 0 }}
                        animate={{ scale: 1 }}
                        exit={{ scale: 0 }}
                        type="button"
                        onClick={handleAbort}
                        whileHover={{ scale: 1.05 }}
                        whileTap={{ scale: 0.95 }}
                        className={cn(
                          'flex h-11 w-11 items-center justify-center rounded-xl',
                          'bg-red-500 text-white',
                          'shadow-lg shadow-red-500/25',
                          'transition-shadow hover:shadow-xl hover:shadow-red-500/30'
                        )}
                      >
                        <Stop size={18} weight="fill" />
                      </motion.button>
                    ) : (
                      <motion.button
                        key="send"
                        initial={{ scale: 0 }}
                        animate={{ scale: 1 }}
                        exit={{ scale: 0 }}
                        type="submit"
                        disabled={!input.trim() || sendBlocked}
                        whileHover={
                          input.trim() && !sendBlocked ? { scale: 1.05 } : {}
                        }
                        whileTap={
                          input.trim() && !sendBlocked ? { scale: 0.95 } : {}
                        }
                        className={cn(
                          'flex h-11 w-11 items-center justify-center rounded-xl',
                          'transition-all duration-200',
                          input.trim() && !sendBlocked
                            ? 'bg-gradient-to-br from-primary-500 to-primary-600 text-white shadow-lg shadow-primary-500/25 hover:shadow-xl hover:shadow-primary-500/30'
                            : 'bg-slate-100 text-slate-400 cursor-not-allowed'
                        )}
                      >
                        <PaperPlaneTilt size={18} weight="fill" />
                      </motion.button>
                    )}
                  </AnimatePresence>
                </div>
              </motion.div>
            </form>

            <div className="mt-3 flex items-center justify-center gap-4 text-xs text-slate-400">
              <span>Press Enter to send</span>
              <span className="h-1 w-1 rounded-full bg-slate-300" />
              <span>Shift+Enter for new line</span>
            </div>
          </div>
        </motion.div>
      </div>
    </AuthGuard>
  );
}

function PendingToolRow({ tool }: { tool: ToolCallView }): JSX.Element {
  return (
    <motion.div
      variants={toolCallVariants}
      initial="hidden"
      animate="visible"
      className={cn(
        'flex items-center gap-3 rounded-xl px-4 py-3',
        'bg-slate-50 border border-slate-200 shadow-sm'
      )}
    >
      <motion.div
        animate={tool.status === 'running' ? { rotate: 360 } : {}}
        transition={tool.status === 'running' ? { repeat: Infinity, duration: 1 } : {}}
        className={cn(
          'flex h-8 w-8 items-center justify-center rounded-lg',
          tool.status === 'success' && 'bg-secondary-100',
          tool.status === 'running' && 'bg-primary-100',
          tool.status === 'error' && 'bg-red-100'
        )}
      >
        {tool.status === 'success' && (
          <CheckCircle size={16} weight="fill" className="text-secondary-600" />
        )}
        {tool.status === 'running' && <CircleNotch size={16} className="text-primary-600" />}
        {tool.status === 'error' && (
          <Warning size={16} weight="fill" className="text-red-600" />
        )}
      </motion.div>
      <div>
        <p className="text-sm font-medium text-slate-700">{tool.name}</p>
        {tool.error && <p className="text-xs text-red-600">{tool.error}</p>}
        {tool.output != null && !tool.error && (
          <p className="text-xs text-slate-500 truncate max-w-md">
            {typeof tool.output === 'string' ? tool.output : JSON.stringify(tool.output)}
          </p>
        )}
      </div>
    </motion.div>
  );
}

function SubagentChipRow({
  chip,
  sessionId,
}: {
  chip: SubagentChipView;
  sessionId?: string;
}): JSX.Element {
  const provider = displaySubagentProvider(chip.provider);
  const href =
    chip.child_session_id && sessionId
      ? `/subagents/${encodeURIComponent(chip.child_session_id)}?session=${encodeURIComponent(sessionId)}`
      : '/subagents';
  return (
    <motion.div
      variants={toolCallVariants}
      initial="hidden"
      animate="visible"
      className={cn(
        'flex items-center gap-3 rounded-xl px-4 py-3',
        'bg-amber-50/80 border border-amber-200 shadow-sm'
      )}
    >
      <div
        className={cn(
          'flex h-8 w-8 items-center justify-center rounded-lg',
          chip.status === 'running' && 'bg-primary-100',
          chip.status === 'completed' && 'bg-secondary-100',
          chip.status === 'failed' && 'bg-red-100'
        )}
      >
        {chip.status === 'running' ? (
          <CircleNotch size={16} className="animate-spin text-primary-600" />
        ) : (
          <GitBranch size={16} weight="fill" className="text-amber-700" />
        )}
      </div>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-slate-800">
          subagent_{chip.status === 'running' ? 'start' : 'end'}
          {chip.label ? ` · ${chip.label}` : ''}
        </p>
        <p className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-slate-500">
          <span className="capitalize">{chip.mode.replace(/_/g, ' ')}</span>
          <span
            className={cn(
              'rounded-full px-1.5 py-0.5 text-[10px] font-semibold',
              provider.isLab
                ? 'bg-amber-100 text-amber-900'
                : 'bg-slate-100 text-slate-700'
            )}
          >
            {provider.label}
            {provider.isLab ? ' · Lab' : ''}
          </span>
          {chip.stop_reason && <span>{chip.stop_reason}</span>}
        </p>
      </div>
      <Link href={href} className="text-xs font-medium text-primary-600 hover:text-primary-700">
        Open
      </Link>
    </motion.div>
  );
}

function MessageBubble({ message }: { message: ChatMessage }): JSX.Element {
  const isUser = message.role === 'user';
  const [copied, setCopied] = useState(false);
  const ts = new Date(message.timestamp);

  const copyContent = () => {
    navigator.clipboard.writeText(message.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <motion.div
      variants={messageVariants}
      initial="hidden"
      animate="visible"
      exit="exit"
      className={cn('flex items-start gap-4', isUser && 'flex-row-reverse')}
    >
      <motion.div
        whileHover={{ scale: 1.05 }}
        className={cn(
          'flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl shadow-md',
          isUser
            ? 'bg-gradient-to-br from-slate-700 to-slate-800 shadow-slate-500/20'
            : 'bg-gradient-to-br from-primary-500 to-primary-600 shadow-primary-500/25'
        )}
      >
        {isUser ? (
          <User size={18} weight="fill" className="text-white" />
        ) : (
          <Robot size={18} weight="fill" className="text-white" />
        )}
      </motion.div>

      <div className={cn('flex-1 space-y-3', isUser && 'flex flex-col items-end')}>
        <motion.div
          whileHover={{ scale: 1.005 }}
          className={cn(
            'group relative rounded-2xl px-5 py-4',
            isUser
              ? 'bg-gradient-to-br from-primary-500 to-primary-600 text-white max-w-[85%] shadow-lg shadow-primary-500/20'
              : 'bg-white border border-slate-200 shadow-md max-w-full'
          )}
        >
          {!isUser && (
            <motion.button
              initial={{ opacity: 0 }}
              whileHover={{ scale: 1.1 }}
              onClick={copyContent}
              className="absolute right-3 top-3 rounded-lg p-1.5 text-slate-400 opacity-0 transition-all group-hover:opacity-100 hover:bg-slate-100 hover:text-slate-600"
            >
              {copied ? <Check size={14} /> : <Copy size={14} />}
            </motion.button>
          )}

          <div className={cn('text-[15px] leading-relaxed', !isUser && 'text-slate-700')}>
            <MessageContent content={message.content} />
          </div>
        </motion.div>

        {message.toolCalls && message.toolCalls.length > 0 && (
          <motion.div
            initial="hidden"
            animate="visible"
            variants={{ visible: { transition: { staggerChildren: 0.1 } } }}
            className="space-y-2"
          >
            {message.toolCalls.map((tool) => (
              <PendingToolRow key={tool.id} tool={tool} />
            ))}
          </motion.div>
        )}

        <p className={cn('text-xs text-slate-400', isUser && 'text-right')}>
          {ts.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
        </p>
      </div>
    </motion.div>
  );
}

function MessageContent({ content }: { content: string }): JSX.Element {
  const parts = content.split(/(```[\s\S]*?```)/g);

  return (
    <>
      {parts.map((part, index) => {
        if (part.startsWith('```')) {
          const match = part.match(/```(\w+)?\n?([\s\S]*?)```/);
          if (match) {
            const [, lang, code] = match;
            return (
              <motion.div
                key={index}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.1 }}
                className="my-4 overflow-hidden rounded-xl bg-slate-900 shadow-lg"
              >
                <div className="flex items-center justify-between border-b border-slate-700 px-4 py-2">
                  <div className="flex items-center gap-2 text-xs text-slate-400">
                    <Code size={12} />
                    {lang || 'code'}
                  </div>
                  <motion.button
                    whileHover={{ scale: 1.1 }}
                    whileTap={{ scale: 0.9 }}
                    onClick={() => navigator.clipboard.writeText(code.trim())}
                    className="rounded p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-200"
                  >
                    <Copy size={12} />
                  </motion.button>
                </div>
                <pre className="overflow-x-auto p-4">
                  <code className="text-sm text-slate-100 font-mono leading-relaxed">
                    {code.trim()}
                  </code>
                </pre>
              </motion.div>
            );
          }
        }
        return (
          <span key={index} className="whitespace-pre-wrap">
            {part}
          </span>
        );
      })}
    </>
  );
}
