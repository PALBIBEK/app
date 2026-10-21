'use client';

/**
 * A session's journal, live: the event stream reduced into chat state, reconnecting
 * when the connection drops and resuming after the last event seen (Last-Event-ID is
 * the event's seq), so nothing is lost or shown twice.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { sessionsAPI, StreamUnauthorized } from '@/lib/api/endpoints/sessions';

import { applyWireEvent, createSessionViewState, type SessionViewState } from './wireEventReducer';

export type Connection = 'connecting' | 'live' | 'reconnecting' | 'closed';

const MAX_BACKOFF_MS = 10_000;

/** 1s, 2s, 4s … up to 10s between attempts. */
export function backoffMs(attempt: number): number {
  return Math.min(MAX_BACKOFF_MS, 1000 * 2 ** Math.max(0, attempt));
}

export function useSessionStream(sessionId: string | undefined, { enabled = true }: { enabled?: boolean } = {}) {
  const [state, setState] = useState<SessionViewState>(() => createSessionViewState());
  const [connection, setConnection] = useState<Connection>('connecting');
  const lastSeq = useRef(-1);

  useEffect(() => {
    setState(createSessionViewState());
    lastSeq.current = -1;
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId || !enabled) {
      setConnection('closed');
      return;
    }
    const ac = new AbortController();
    let attempt = 0;

    void (async () => {
      while (!ac.signal.aborted) {
        setConnection(attempt === 0 ? 'connecting' : 'reconnecting');
        try {
          const resume = lastSeq.current >= 0 ? String(lastSeq.current) : undefined;
          const onOpen = () => {
            setConnection('live');
            attempt = 0;
          };
          for await (const event of sessionsAPI.openEventStream(sessionId, { signal: ac.signal, lastEventId: resume, onOpen })) {
            lastSeq.current = Math.max(lastSeq.current, event.seq);
            setState((prev) => applyWireEvent(prev, event));
          }
        } catch (error) {
          if (ac.signal.aborted) return;
          if (error instanceof StreamUnauthorized) {
            // The API client refreshes an expired token on its own 401; then reconnect.
            await sessionsAPI.getById(sessionId).catch(() => undefined);
          }
        }
        if (ac.signal.aborted) return;
        // The stream ended or failed: try again after a pause, from where it stopped.
        setConnection('reconnecting');
        await new Promise((resolve) => setTimeout(resolve, backoffMs(attempt)));
        attempt += 1;
      }
    })();

    return () => ac.abort();
  }, [sessionId, enabled]);

  /** Show a message the user just sent until the journal confirms it. */
  const addLocalMessage = useCallback((text: string) => {
    setState((prev) => ({
      ...prev,
      messages: [...prev.messages, { id: `local-${Date.now()}`, role: 'user', content: text, timestamp: new Date().toISOString() }],
      error: null,
    }));
  }, []);

  return { state, setState, connection, addLocalMessage };
}
