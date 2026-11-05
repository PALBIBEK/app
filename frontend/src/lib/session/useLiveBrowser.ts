'use client';

/**
 * The agent's browser, live, while it is watched: its screen refreshed about once a second
 * (only while the page is visible, each look after the last has come back), and a person's
 * inputs sent in order, typing merged, each answered with the screen after it.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { sessionsAPI } from '../api/endpoints/sessions';
import { merged, type BrowserInput, type BrowserScreen } from './liveBrowser';

export const REFRESH_MS = 1000;

export interface LiveBrowser {
  screen: BrowserScreen | null;
  /** Why the browser cannot be shown or acted on now, in the supervisor's words. */
  error: string | null;
  send(input: BrowserInput): void;
}

const said = (error: unknown): string => (error instanceof Error ? error.message : 'The browser could not be reached.');

export function useLiveBrowser(sessionId: string, watching: boolean): LiveBrowser {
  const [screen, setScreen] = useState<BrowserScreen | null>(null);
  const [error, setError] = useState<string | null>(null);
  const queue = useRef<BrowserInput[]>([]);
  const acting = useRef(false);

  useEffect(() => {
    if (!watching) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const look = async (): Promise<void> => {
      if (document.visibilityState === 'visible' && !acting.current) {
        try {
          const shown = await sessionsAPI.browserScreen(sessionId);
          if (live && !acting.current) {
            setScreen(shown);
            setError(null);
          }
        } catch (e) {
          if (live) setError(said(e));
        }
      }
      if (live) timer = setTimeout(() => void look(), REFRESH_MS);
    };
    void look();
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [sessionId, watching]);

  const drain = useCallback(async (): Promise<void> => {
    if (acting.current) return;
    acting.current = true;
    try {
      while (queue.current.length) {
        for (const input of merged(queue.current.splice(0))) {
          try {
            setScreen(await sessionsAPI.browserInput(sessionId, input));
            setError(null);
          } catch (e) {
            setError(said(e));
            queue.current = []; // what came after a refused input was meant for a page that did not change
            return;
          }
        }
      }
    } finally {
      acting.current = false;
    }
  }, [sessionId]);

  const send = useCallback(
    (input: BrowserInput) => {
      queue.current.push(input);
      void drain();
    },
    [drain],
  );

  return { screen, error, send };
}
