/**
 * sessions.test.ts — product BFF sessions API (D4).
 */

import { describe, it, expect, beforeEach, vi, Mock } from 'vitest';
import { apiClient } from '../client';
import { sessionsAPI } from './sessions';
import type { Session, SessionListResponse, SessionMessageResponse } from '../types/session.types';

vi.mock('../client', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}));

vi.mock('../../store/authStore', () => ({
  useAuthStore: {
    getState: () => ({ accessToken: 'test-token' }),
  },
}));

const createMockSession = (overrides: Partial<Session> = {}): Session => ({
  id: 'session-123',
  harness_id: 'harness-456',
  status: 'active',
  harness_version: '1.0.0',
  runtime: 'echo',
  composition_id: null,
  composition_digest: null,
  title: 'Test',
  message_count: 5,
  total_cost: 0.05,
  last_seq: 3,
  is_busy: false,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:05:00Z',
  pin: {
    harness_version: '1.0.0',
    runtime: 'echo',
    composition_id: null,
    composition_digest: null,
  },
  ...overrides,
});

describe('sessionsAPI', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('list', () => {
    it('should fetch sessions list', async () => {
      const mockResponse: SessionListResponse = {
        data: [createMockSession(), createMockSession({ id: 'session-456' })],
        total: 2,
        page: 1,
        page_size: 20,
      };
      (apiClient.get as Mock).mockResolvedValue(mockResponse);

      const result = await sessionsAPI.list();

      expect(apiClient.get).toHaveBeenCalledWith('/sessions', { params: undefined });
      expect(result.data).toHaveLength(2);
      expect(result.total).toBe(2);
    });

    it('should pass filter parameters', async () => {
      const params = { harness_id: 'harness-123', page: 1, page_size: 10 };
      const mockResponse: SessionListResponse = {
        data: [],
        total: 0,
        page: 1,
        page_size: 10,
      };
      (apiClient.get as Mock).mockResolvedValue(mockResponse);

      await sessionsAPI.list(params);

      expect(apiClient.get).toHaveBeenCalledWith('/sessions', { params });
    });
  });

  describe('getById', () => {
    it('should fetch session by ID', async () => {
      const mockResponse = createMockSession();
      (apiClient.get as Mock).mockResolvedValue(mockResponse);

      const result = await sessionsAPI.getById('session-123');

      expect(apiClient.get).toHaveBeenCalledWith('/sessions/session-123');
      expect(result.id).toBe('session-123');
    });

    it('should validate response with Zod schema', async () => {
      (apiClient.get as Mock).mockResolvedValue({ id: 'session-123' });

      await expect(sessionsAPI.getById('session-123')).rejects.toThrow();
    });
  });

  describe('create', () => {
    it('should create a new session with harness_id', async () => {
      const mockResponse = createMockSession({ harness_id: 'harness-123' });
      (apiClient.post as Mock).mockResolvedValue(mockResponse);

      const result = await sessionsAPI.create({ harness_id: 'harness-123' });

      expect(apiClient.post).toHaveBeenCalledWith('/sessions', {
        harness_id: 'harness-123',
      });
      expect(result.harness_id).toBe('harness-123');
    });
  });

  describe('sendMessage', () => {
    it('should send message to session', async () => {
      const mockResponse: SessionMessageResponse = {
        turn_id: 'turn-1',
        command_id: 'cmd-1',
        status: 'accepted',
      };
      (apiClient.post as Mock).mockResolvedValue(mockResponse);

      const result = await sessionsAPI.sendMessage('session-123', 'Hello world');

      expect(apiClient.post).toHaveBeenCalledWith('/sessions/session-123/messages', {
        content: 'Hello world',
      });
      expect(result.turn_id).toBe('turn-1');
      expect(result.status).toBe('accepted');
    });

    it('should include command_id when provided', async () => {
      const mockResponse: SessionMessageResponse = {
        turn_id: 'turn-1',
        command_id: 'cmd-xyz',
        status: 'accepted',
      };
      (apiClient.post as Mock).mockResolvedValue(mockResponse);

      await sessionsAPI.sendMessage('session-123', 'Hello', 'cmd-xyz');

      expect(apiClient.post).toHaveBeenCalledWith('/sessions/session-123/messages', {
        content: 'Hello',
        command_id: 'cmd-xyz',
      });
    });
  });

  describe('abort', () => {
    it('should abort a session turn', async () => {
      (apiClient.post as Mock).mockResolvedValue(undefined);

      await sessionsAPI.abort('session-123');

      expect(apiClient.post).toHaveBeenCalledWith('/sessions/session-123/abort');
    });
  });

  describe('delete', () => {
    it('should delete a session', async () => {
      (apiClient.delete as Mock).mockResolvedValue(undefined);

      await sessionsAPI.delete('session-123');

      expect(apiClient.delete).toHaveBeenCalledWith('/sessions/session-123');
    });
  });
});

describe('sessionsAPI.openEventStream', () => {
  const frame = (seq: number) =>
    `id: ${seq}\ndata: ${JSON.stringify({ seq, event_id: `e${seq}`, type: 'message_end', ts_ms: 1, data: {} })}\n\n`;

  function serve(body: string): void {
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(body));
        controller.close();
      },
    });
    vi.stubGlobal('fetch', vi.fn(async () => ({ status: 200, ok: true, body: stream })));
  }

  it('yields events, skips pings, and says when the session has ended', async () => {
    serve(`${frame(0)}: ping\n\n${frame(1)}event: end\ndata: {}\n\n${frame(2)}`);
    const onEnded = vi.fn();
    const seen: number[] = [];
    for await (const event of sessionsAPI.openEventStream('s1', { onEnded })) seen.push(event.seq);
    expect(seen).toEqual([0, 1]); // nothing after the end is read
    expect(onEnded).toHaveBeenCalledOnce();
    vi.unstubAllGlobals();
  });

  it('does not take a stream that merely closed for an ended session', async () => {
    serve(frame(0));
    const onEnded = vi.fn();
    for await (const _event of sessionsAPI.openEventStream('s1', { onEnded })) {
      // drain
    }
    expect(onEnded).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });

  it('says a session that is gone (erased) has ended, instead of failing to reconnect', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ status: 410, ok: false, body: null })));
    const onEnded = vi.fn();
    const seen: unknown[] = [];
    for await (const event of sessionsAPI.openEventStream('s1', { onEnded })) seen.push(event);
    expect(seen).toEqual([]);
    expect(onEnded).toHaveBeenCalledOnce();
    vi.unstubAllGlobals();
  });
});
