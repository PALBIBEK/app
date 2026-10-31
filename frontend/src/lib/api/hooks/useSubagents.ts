/**
 * A session's subagents, kept fresh: polled while one is starting or working (or the
 * session is), since they change without the session's own stream saying so.
 */

import { useQuery } from '@tanstack/react-query';

import { anyActive } from '@/lib/session/subagents';

import { sessionsAPI } from '../endpoints/sessions';
import type { Subagent } from '../types/session.types';

export const subagentKeys = {
  list: (sessionId: string) => ['sessions', 'subagents', sessionId] as const,
};

const ACTIVE_POLL_MS = 3_000;
const IDLE_POLL_MS = 30_000;

export function useSubagents(sessionId: string | undefined, { sessionRunning = false }: { sessionRunning?: boolean } = {}) {
  return useQuery({
    queryKey: subagentKeys.list(sessionId ?? ''),
    queryFn: () => sessionsAPI.subagents(sessionId!),
    enabled: !!sessionId,
    refetchInterval: (query) => {
      const listed = (query.state.data as Subagent[] | undefined) ?? [];
      return sessionRunning || anyActive(listed) ? ACTIVE_POLL_MS : IDLE_POLL_MS;
    },
  });
}
