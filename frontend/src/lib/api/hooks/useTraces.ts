/**
 * Run traces: React Query hooks. A run still going refreshes itself every few seconds;
 * a finished one never changes, so it is cached for good.
 */

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';

import { useAuthStore } from '@/lib/store/authStore';

import { tracesAPI } from '../endpoints/traces';
import type { JudgeInput, RunStatus, TraceListParams, TraceScore } from '../types/trace.types';

const LIVE: readonly RunStatus[] = ['running', 'awaiting_approval'];
const LIVE_REFRESH_MS = 4000;

export const traceKeys = {
  all: ['traces'] as const,
  list: (params: TraceListParams) => [...traceKeys.all, 'list', params] as const,
  session: (sessionId: string) => [...traceKeys.all, 'session', sessionId] as const,
  run: (sessionId: string, runId: string) => [...traceKeys.all, 'run', sessionId, runId] as const,
  span: (sessionId: string, runId: string, spanId: string) =>
    [...traceKeys.all, 'span', sessionId, runId, spanId] as const,
  events: (sessionId: string, runId: string) => [...traceKeys.all, 'events', sessionId, runId] as const,
  agentVersions: (agentId: string) => [...traceKeys.all, 'agent-versions', agentId] as const,
  issues: (agentId: string) => [...traceKeys.all, 'issues', agentId] as const,
  judges: (agentId: string) => [...traceKeys.all, 'judges', agentId] as const,
};

/** The user's runs, newest first, a page at a time. */
export function useTraceList(params: Omit<TraceListParams, 'before_ms'>) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  return useInfiniteQuery({
    queryKey: traceKeys.list(params),
    queryFn: ({ pageParam }) => tracesAPI.list({ ...params, before_ms: pageParam ?? undefined }),
    initialPageParam: null as number | null,
    getNextPageParam: (last) => last.next_before_ms,
    enabled: isAuthenticated,
    refetchInterval: (query) =>
      query.state.data?.pages.some((p) => p.items.some((r) => LIVE.includes(r.status))) ? LIVE_REFRESH_MS : false,
  });
}

export function useSessionTraces(sessionId: string | undefined) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  return useQuery({
    queryKey: traceKeys.session(sessionId ?? ''),
    queryFn: () => tracesAPI.session(sessionId!),
    enabled: isAuthenticated && !!sessionId,
    refetchInterval: (query) =>
      query.state.data?.items.some((r) => LIVE.includes(r.status)) ? LIVE_REFRESH_MS : false,
  });
}

export function useRunTrace(sessionId: string | undefined, runId: string | undefined) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  return useQuery({
    queryKey: traceKeys.run(sessionId ?? '', runId ?? ''),
    queryFn: () => tracesAPI.run(sessionId!, runId!),
    enabled: isAuthenticated && !!sessionId && !!runId,
    retry: false,
    refetchInterval: (query) =>
      query.state.data && LIVE.includes(query.state.data.rollup.status) ? LIVE_REFRESH_MS : false,
  });
}

/** One span's full content; fetched when it is opened. */
export function useTraceSpan(sessionId: string, runId: string, spanId: string | null, live: boolean) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  return useQuery({
    queryKey: traceKeys.span(sessionId, runId, spanId ?? ''),
    queryFn: () => tracesAPI.span(sessionId, runId, spanId!),
    enabled: isAuthenticated && !!spanId,
    staleTime: live ? 0 : Infinity,
    retry: false,
  });
}

export function useRunEvents(sessionId: string, runId: string, enabled: boolean) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  return useQuery({
    queryKey: traceKeys.events(sessionId, runId),
    queryFn: () => tracesAPI.events(sessionId, runId),
    enabled: isAuthenticated && enabled,
    retry: false,
  });
}

/** Say how a run (or one span) went, or take it back (``good`` null). */
export function useFeedback(sessionId: string, runId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({
      good,
      spanId,
      comment,
    }: {
      good: boolean | null;
      spanId?: string | null;
      comment?: string | null;
    }): Promise<TraceScore | null> => {
      if (good === null) {
        await tracesAPI.clearFeedback(sessionId, runId, spanId);
        return null;
      }
      return tracesAPI.setFeedback(sessionId, runId, { good, span_id: spanId ?? null, comment: comment ?? null });
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: traceKeys.run(sessionId, runId) });
      void client.invalidateQueries({ queryKey: [...traceKeys.all, 'list'] });
      void client.invalidateQueries({ queryKey: [...traceKeys.all, 'agent-versions'] });
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : 'Could not save that'),
  });
}

export function useAgentQuality(agentId: string | undefined) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  return useQuery({
    queryKey: traceKeys.agentVersions(agentId ?? ''),
    queryFn: () => tracesAPI.agentVersions(agentId!),
    enabled: isAuthenticated && !!agentId,
    staleTime: 30_000,
  });
}

export function useIssues(agentId: string | undefined) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  return useQuery({
    queryKey: traceKeys.issues(agentId ?? ''),
    queryFn: () => tracesAPI.issues(agentId || undefined),
    enabled: isAuthenticated,
    staleTime: 15_000,
  });
}

export function useJudges(agentId: string | undefined) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  return useQuery({
    queryKey: traceKeys.judges(agentId ?? ''),
    queryFn: () => tracesAPI.judges(agentId!),
    enabled: isAuthenticated && !!agentId,
  });
}

export function useSaveJudge(agentId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: JudgeInput) => tracesAPI.saveJudge(agentId, body),
    onSuccess: (judge) => {
      toast.success(`Judge “${judge.name}” saved (version ${judge.version})`);
      void client.invalidateQueries({ queryKey: traceKeys.judges(agentId) });
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : 'Could not save the judge'),
  });
}

export function useDeleteJudge(agentId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (judgeId: string) => tracesAPI.deleteJudge(agentId, judgeId),
    onSuccess: () => void client.invalidateQueries({ queryKey: traceKeys.judges(agentId) }),
    onError: (err) => toast.error(err instanceof Error ? err.message : 'Could not remove the judge'),
  });
}
