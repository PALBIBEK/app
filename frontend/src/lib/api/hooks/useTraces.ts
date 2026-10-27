/**
 * Run traces: React Query hooks. A run still going refreshes itself every few seconds;
 * a finished one never changes, so it is cached for good.
 */

import { useInfiniteQuery, useQuery } from '@tanstack/react-query';

import { useAuthStore } from '@/lib/store/authStore';

import { tracesAPI } from '../endpoints/traces';
import type { RunStatus, TraceListParams } from '../types/trace.types';

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
