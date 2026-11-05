'use client';

/**
 * A person's answers to the surfaces the agent drew. A button's event is sent once
 * (`send`); its surface is closed from the click until the agent draws it again, the way
 * the supervisor takes one answer per drawing (./reports).
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';

import type { ChatMessage } from '../api/types/session.types';
import { answeredSurfaces, type UiAction } from './reports';
import type { ReportAction } from './useReports';

export type SurfaceAnswer = { state: 'open' } | { state: 'sending' } | { state: 'answered'; action: UiAction };

export interface SurfaceAnswers {
  /** Whether a person can answer here at all (a subagent's conversation is only read). */
  canAnswer: boolean;
  of(surfaceId: string): SurfaceAnswer;
  /** The processor's action handler: a button was clicked. */
  onAction(action: ReportAction): void;
}

export function useSurfaceAnswers(
  messages: readonly ChatMessage[],
  send: ((action: ReportAction) => Promise<void>) | undefined,
): SurfaceAnswers {
  const answered = useMemo(() => answeredSurfaces(messages), [messages]);
  /** Sent, and not yet in the conversation as an answer. */
  const [sent, setSent] = useState<ReadonlySet<string>>(new Set());

  useEffect(() => {
    // An answer in the conversation speaks for itself; then the surface's next drawing reopens it.
    if ([...sent].some((s) => answered.has(s))) setSent((now) => new Set([...now].filter((s) => !answered.has(s))));
  }, [answered, sent]);

  const onAction = useCallback(
    (action: ReportAction) => {
      if (!send || answered.has(action.surfaceId) || sent.has(action.surfaceId)) return;
      setSent((now) => new Set(now).add(action.surfaceId));
      send(action).catch((error: unknown) => {
        setSent((now) => new Set([...now].filter((s) => s !== action.surfaceId)));
        toast.error(error instanceof Error ? error.message : 'The answer was not sent.');
      });
    },
    [answered, send, sent],
  );

  return useMemo<SurfaceAnswers>(
    () => ({
      canAnswer: send !== undefined,
      of: (surfaceId) => {
        const action = answered.get(surfaceId);
        if (action) return { state: 'answered', action };
        return sent.has(surfaceId) ? { state: 'sending' } : { state: 'open' };
      },
      onAction,
    }),
    [answered, onAction, send, sent],
  );
}
