/**
 * Run traces through the BFF: the user's runs, one session's runs, one run's span tree,
 * one span's full content, and the journal events a trace was built from.
 */

import { apiClient } from '../client';
import type {
  AgentQuality,
  RawEvent,
  RunTrace,
  SessionTraces,
  TraceList,
  TraceListParams,
  TraceScore,
  TraceSpan,
} from '../types/trace.types';

const seg = encodeURIComponent;

/** Query string for the runs list; repeated `status` keys, as the API reads a list. */
export function traceListQuery(params: TraceListParams = {}): string {
  const q = new URLSearchParams();
  for (const s of params.status ?? []) q.append('status', s);
  if (params.agent_id) q.set('agent_id', params.agent_id);
  if (params.errors) q.set('errors', 'true');
  if (params.denied) q.set('denied', 'true');
  if (params.failing_checks) q.set('failing_checks', 'true');
  if (params.feedback) q.set('feedback', params.feedback);
  if (params.q?.trim()) q.set('q', params.q.trim());
  if (params.since_ms != null) q.set('since_ms', String(params.since_ms));
  if (params.before_ms != null) q.set('before_ms', String(params.before_ms));
  if (params.limit != null) q.set('limit', String(params.limit));
  const text = q.toString();
  return text ? `?${text}` : '';
}

export const tracesAPI = {
  list: (params?: TraceListParams): Promise<TraceList> => apiClient.get<TraceList>(`/traces${traceListQuery(params)}`),

  session: (sessionId: string): Promise<SessionTraces> =>
    apiClient.get<SessionTraces>(`/traces/sessions/${seg(sessionId)}`),

  run: (sessionId: string, runId: string): Promise<RunTrace> =>
    apiClient.get<RunTrace>(`/traces/sessions/${seg(sessionId)}/runs/${seg(runId)}`),

  span: (sessionId: string, runId: string, spanId: string): Promise<TraceSpan> =>
    apiClient.get<TraceSpan>(`/traces/sessions/${seg(sessionId)}/runs/${seg(runId)}/spans/${seg(spanId)}`),

  events: (sessionId: string, runId: string): Promise<{ events: RawEvent[] }> =>
    apiClient.get<{ events: RawEvent[] }>(`/traces/sessions/${seg(sessionId)}/runs/${seg(runId)}/events`),

  /** The user's verdict on the run, or on one span; saying it again replaces it. */
  setFeedback: (
    sessionId: string,
    runId: string,
    body: { good: boolean; span_id?: string | null; comment?: string | null },
  ): Promise<TraceScore> =>
    apiClient.put<TraceScore>(`/traces/sessions/${seg(sessionId)}/runs/${seg(runId)}/feedback`, body),

  clearFeedback: (sessionId: string, runId: string, spanId?: string | null): Promise<void> =>
    apiClient.delete<void>(
      `/traces/sessions/${seg(sessionId)}/runs/${seg(runId)}/feedback${spanId ? `?span_id=${seg(spanId)}` : ''}`,
    ),

  /** How each version of an agent did across the user's finished runs. */
  agentVersions: (agentId: string): Promise<AgentQuality> =>
    apiClient.get<AgentQuality>(`/traces/agents/${seg(agentId)}/versions`),
};
