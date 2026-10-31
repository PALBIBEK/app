/**
 * Approvals a session's subagents wait on. A subagent's calls ask a person like the
 * session's own, but in its own journal, so the session's stream never shows them: they
 * are read from the session's approvals (which include its subagents') while they may come.
 */

import { useCallback, useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import { sessionsAPI } from '../endpoints/sessions';

export interface SubagentApproval {
  approval_id: string;
  prompt: string;
  options: string[];
  subagent_label: string | null;
}

const POLL_MS = 3_000;

export function useSubagentApprovals(
  sessionId: string,
  { active }: { active: boolean },
): { approvals: SubagentApproval[]; answered: (approvalId: string) => void } {
  const [done, setDone] = useState<Set<string>>(() => new Set());
  const { data, refetch } = useQuery({
    queryKey: ['sessions', 'subagent-approvals', sessionId],
    queryFn: () => sessionsAPI.listApprovals(sessionId),
    enabled: !!sessionId && active,
    refetchInterval: active ? POLL_MS : false,
  });
  /** One was answered: it goes now, not at the next poll. */
  const answered = useCallback(
    (approvalId: string) => {
      setDone((prev) => new Set(prev).add(approvalId));
      void refetch();
    },
    [refetch],
  );
  const approvals = active
    ? (data ?? [])
        // The listing holds only those still waiting, the session's own among them (its stream shows those).
        .filter((a) => a.subagent && !done.has(a.approval_id))
        .map((a) => ({ approval_id: a.approval_id, prompt: a.prompt, options: a.options, subagent_label: a.subagent_label ?? null }))
    : [];
  return { approvals, answered };
}
