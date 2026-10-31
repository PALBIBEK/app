/**
 * sessionsAPI — product BFF only (never supervisor host).
 */

import { apiClient } from '../client';
import type { SessionPins } from '../types/catalog.types';
import type { SessionStartup } from '@/lib/session/startup';
import { useAuthStore } from '../../store/authStore';
import {
  Run,
  RunSchema,
  Session,
  SessionCreateRequest,
  SessionListResponse,
  SessionListResponseSchema,
  SessionMessageResponse,
  SessionMessageResponseSchema,
  SessionSchema,
  Subagent,
  SubagentListSchema,
  WireEvent,
  WireEventSchema,
} from '../types/session.types';

const API_BASE =
  typeof process !== 'undefined'
    ? process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000/api/v1'
    : 'http://localhost:8000/api/v1';

export const sessionsAPI = {
  list: async (params?: {
    harness_id?: string;
    page?: number;
    page_size?: number;
  }): Promise<SessionListResponse> => {
    const response = await apiClient.get<SessionListResponse>('/sessions', { params });
    return SessionListResponseSchema.parse(response);
  },

  getById: async (id: string): Promise<Session> => {
    const response = await apiClient.get<Session>(`/sessions/${id}`);
    return SessionSchema.parse(response);
  },

  create: async (data: SessionCreateRequest): Promise<Session> => {
    const response = await apiClient.post<Session>('/sessions', {
      harness_id: data.harness_id,
      ...(data.scope_id ? { scope_id: data.scope_id } : {}),
    });
    return SessionSchema.parse(response);
  },

  sendMessage: async (
    sessionId: string,
    content: string,
    commandId?: string
  ): Promise<SessionMessageResponse> => {
    const response = await apiClient.post<SessionMessageResponse>(
      `/sessions/${sessionId}/messages`,
      {
        content,
        ...(commandId ? { command_id: commandId } : {}),
      }
    );
    return SessionMessageResponseSchema.parse(response);
  },

  /** The session's runs, newest first. */
  runs: async (sessionId: string, limit = 20): Promise<Run[]> => {
    const response = await apiClient.get<unknown[]>(`/sessions/${encodeURIComponent(sessionId)}/runs`, {
      params: { limit },
    });
    return RunSchema.array().parse(response);
  },

  /** One run by the turn_id its message got (the server answers 202 until it has ended). */
  getRun: async (sessionId: string, runId: string): Promise<Run> =>
    RunSchema.parse(
      await apiClient.get(`/sessions/${encodeURIComponent(sessionId)}/runs/${encodeURIComponent(runId)}`)
    ),

  /** Stop a run; a queued one never runs. Safe to repeat: an ended run comes back unchanged. */
  cancelRun: async (sessionId: string, runId: string): Promise<Run> =>
    RunSchema.parse(
      await apiClient.post(`/sessions/${encodeURIComponent(sessionId)}/runs/${encodeURIComponent(runId)}/cancel`)
    ),

  abort: async (sessionId: string): Promise<void> => {
    await apiClient.post(`/sessions/${sessionId}/abort`);
  },

  fork: async (sessionId: string, atSeq?: number): Promise<{ id: string; parent_id: string }> => {
    return apiClient.post(`/sessions/${sessionId}/fork`, {
      ...(atSeq != null ? { at_seq: atSeq } : {}),
    });
  },

  /**
   * BFF tools invoke — never call supervisor host from the browser (D6 F).
   * POST /sessions/:id/tools/invoke
   */
  /** What the session runs, part by part (404 for a session on a bare harness). */
  pins: (sessionId: string): Promise<SessionPins> =>
    apiClient.get<SessionPins>(`/sessions/${encodeURIComponent(sessionId)}/pins`),

  /** Where a new session's start stands: poll after create until `ready` or `failed`. */
  startup: (sessionId: string): Promise<SessionStartup> =>
    apiClient.get<SessionStartup>(`/sessions/${encodeURIComponent(sessionId)}/startup`),

  invokeTool: async (
    sessionId: string,
    body: { name: string; arguments?: Record<string, unknown>; tool_call_id?: string }
  ): Promise<{
    tool_call_id: string;
    ok: boolean;
    payload?: unknown;
    error?: string | null;
    code?: string | null;
  }> => {
    return apiClient.post(`/sessions/${sessionId}/tools/invoke`, body);
  },

  /**
   * List pending approvals for a session via BFF (D6 E — no browser→supervisor).
   * GET /sessions/:id/approvals
   */
  listApprovals: async (
    sessionId: string
  ): Promise<
    Array<{
      approval_id: string;
      session_id: string;
      prompt: string;
      options: string[];
      status: string;
      choice?: string | null;
      timeout_s: number;
      /** A call of one of the session's subagents waits (answered from the session all the same). */
      subagent?: boolean;
      subagent_label?: string | null;
    }>
  > => {
    return apiClient.get(`/sessions/${sessionId}/approvals`);
  },

  /**
   * Respond to a pending approval via BFF.
   * POST /sessions/:id/approvals/:approvalId
   */
  respondApproval: async (
    sessionId: string,
    approvalId: string,
    choice: string
  ): Promise<{
    approval_id: string;
    session_id: string;
    prompt: string;
    options: string[];
    status: string;
    choice?: string | null;
    timeout_s: number;
  }> => {
    return apiClient.post(`/sessions/${sessionId}/approvals/${approvalId}`, {
      choice,
    });
  },

  /**
   * Every subagent below the session (its own, theirs, and so on), via the BFF.
   * GET /sessions/:id/subagents
   */
  subagents: async (sessionId: string): Promise<Subagent[]> => {
    const response = await apiClient.get(`/sessions/${encodeURIComponent(sessionId)}/subagents`);
    return SubagentListSchema.parse(response).subagents;
  },

  /** The next turn of a continuable subagent (any other takes no messages: 409). */
  messageSubagent: async (sessionId: string, subagentId: string, content: string): Promise<SessionMessageResponse> => {
    const response = await apiClient.post(
      `/sessions/${encodeURIComponent(sessionId)}/subagents/${encodeURIComponent(subagentId)}/messages`,
      { content },
    );
    return SessionMessageResponseSchema.parse(response);
  },

  /** Stop what a subagent is doing; the agent that started it is told it was stopped. */
  stopSubagent: async (sessionId: string, subagentId: string): Promise<void> => {
    await apiClient.post(`/sessions/${encodeURIComponent(sessionId)}/subagents/${encodeURIComponent(subagentId)}/abort`);
  },

  /** End the session; its conversation is kept. */
  delete: async (sessionId: string): Promise<void> => {
    await apiClient.delete(`/sessions/${sessionId}`);
  },

  /** End the session and erase its conversation, files and history for good. */
  erase: async (sessionId: string): Promise<void> => {
    await apiClient.delete(`/sessions/${sessionId}/data`);
  },

  /**
   * Authenticated SSE via fetch (EventSource cannot set Bearer).
   */
  openEventStream: async function* (
    sessionId: string,
    options?: {
      lastEventId?: string;
      signal?: AbortSignal;
      onOpen?: () => void;
      onEnded?: () => void;
      /** Stream this subagent's transcript (a subagent below the session) instead. */
      subagentId?: string;
    }
  ): AsyncGenerator<WireEvent, void, unknown> {
    const accessToken = useAuthStore.getState().accessToken;
    if (!accessToken) {
      throw new Error('Not authenticated');
    }
    const headers: Record<string, string> = {
      Authorization: `Bearer ${accessToken}`,
      Accept: 'text/event-stream',
    };
    if (options?.lastEventId) {
      headers['Last-Event-ID'] = options.lastEventId;
    }

    const path = options?.subagentId
      ? `/sessions/${encodeURIComponent(sessionId)}/subagents/${encodeURIComponent(options.subagentId)}/events`
      : `/sessions/${sessionId}/events`;
    const response = await fetch(`${API_BASE}${path}`, {
      method: 'GET',
      headers,
      credentials: 'include',
      signal: options?.signal,
    });
    if (response.status === 401) {
      throw new StreamUnauthorized();
    }
    if (response.status === 404 || response.status === 410) {
      // Erased (410), or no longer there: nothing will ever stream, so do not reconnect.
      options?.onEnded?.();
      return;
    }
    if (!response.ok || !response.body) {
      throw new Error(`SSE failed: ${response.status}`);
    }
    options?.onOpen?.();

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let lastId: string | undefined = options?.lastEventId;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split('\n\n');
      buffer = parts.pop() ?? '';
      for (const part of parts) {
        const lines = part.split('\n');
        let data = '';
        let id = lastId;
        let kind = '';
        for (const line of lines) {
          if (line.startsWith('id:')) {
            id = line.slice(3).trim();
          } else if (line.startsWith('data:')) {
            data += line.slice(5).trim();
          } else if (line.startsWith('event:')) {
            kind = line.slice(6).trim();
          }
        }
        if (kind === 'end') {
          // The session has ended and every event of it was sent: nothing will follow.
          options?.onEnded?.();
          return;
        }
        if (!data) continue;
        try {
          const parsed = WireEventSchema.parse(JSON.parse(data));
          if (id) lastId = id;
          yield parsed;
        } catch {
          // skip malformed / heartbeats
        }
      }
    }
  },
};

/** The stream's token expired: refresh it (any API call does) and reconnect. */
export class StreamUnauthorized extends Error {
  constructor() {
    super('Event stream: not authorized');
  }
}
