/**
 * Approvals: every question a paused run is waiting on, across the user's sessions,
 * answered from here or opened in its session for context.
 */

'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { ShieldCheck } from '@phosphor-icons/react';
import { toast } from 'sonner';

import { AppShell, PageBody } from '@/components/layout/AppShell';
import { Button, Card, EmptyState, ErrorNote, LinkButton, Mono, PageHeader, SkeletonRows } from '@/components/ui';
import { sessionsAPI } from '@/lib/api/endpoints/sessions';
import { getApiErrorMessage } from '@/lib/api/errors';
import { useProposals } from '@/lib/api/hooks/useEvaluations';
import { operateKeys, useApprovalInbox } from '@/lib/api/hooks/useOperate';
import type { InboxItem } from '@/lib/api/types/operate.types';
import { formatDuration } from '@/lib/utils';

const ALLOW = /^(approve|allow|yes|y|ok)$/i;

function useNow(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);
  return now;
}

function Row({ item, now, onAnswered }: { item: InboxItem; now: number; onAnswered: () => void }): JSX.Element {
  const [busy, setBusy] = useState<string | null>(null);
  const left = item.expires_at_ms ? Math.max(0, item.expires_at_ms - now) : null;
  const ordered = [...item.options].sort((a, b) => Number(ALLOW.test(b)) - Number(ALLOW.test(a)));

  async function answer(choice: string): Promise<void> {
    setBusy(choice);
    try {
      await sessionsAPI.respondApproval(item.session_id, item.approval_id, choice);
      toast.success(`Answered “${choice}”`);
      onAnswered();
    } catch (err) {
      toast.error(getApiErrorMessage(err, 'Could not answer'));
    } finally {
      setBusy(null);
    }
  }

  return (
    <li className="flex flex-col gap-3 border-b border-line-soft px-5 py-4 last:border-0" data-testid="inbox-item">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <Link href={`/sessions/${item.session_id}`} className="font-medium text-ink hover:text-accent-700">
          {item.session_title || `${item.harness_name ?? 'Agent'} session`}
        </Link>
        {item.harness_name ? <Mono className="text-ink-2">{item.harness_name}</Mono> : null}
        {item.subagent ? (
          <span data-testid="inbox-subagent" className="rounded-full bg-line-soft px-2 py-px text-xs text-ink-2">
            Asked by its subagent{item.subagent_label ? ` · ${item.subagent_label}` : ''}
          </span>
        ) : null}
        <Mono className="text-xs text-ink-3">{item.session_id.slice(0, 13)}</Mono>
        <span className="flex-1" />
        <span className="tabular font-mono text-xs text-ink-3">
          waiting {item.created_at_ms ? formatDuration(now - item.created_at_ms) : '—'}
          {left != null ? ` · ${left > 0 ? `${formatDuration(left)} left` : 'timing out'}` : ''}
        </span>
      </div>
      <p className="whitespace-pre-wrap break-words rounded-lg border border-line-soft bg-canvas px-3.5 py-2.5 font-mono text-[12.5px]">{item.prompt}</p>
      <div className="flex flex-wrap items-center gap-2">
        {ordered.map((o, i) => (
          <Button key={o} size="sm" variant={i === 0 && ALLOW.test(o) ? 'primary' : 'secondary'} disabled={busy !== null} onClick={() => void answer(o)}>
            {busy === o ? '…' : o}
          </Button>
        ))}
        <span className="flex-1" />
        <LinkButton href={`/sessions/${item.session_id}`} size="sm" variant="ghost">
          Open session
        </LinkButton>
      </div>
    </li>
  );
}

/** Changes learned from traces that passed their gate: each publishes only when approved here. */
function ChangesToApprove(): JSX.Element | null {
  const { data } = useProposals({ status: 'awaiting_approval' });
  if (!data?.length) return null;
  return (
    <Card tone="accent" aria-label="Changes to approve">
      <div className="border-b border-line-soft px-5 py-3">
        <h2 className="text-[15px] font-semibold">Changes to approve</h2>
        <p className="text-sm text-ink-2">Learned from failing runs and measured on saved ones; each did better and no worse.</p>
      </div>
      <ul>
        {data.map((p) => (
          <li key={p.id} className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-line-soft px-5 py-3.5 last:border-0" data-testid="change-to-approve">
            <span className="font-medium">
              {p.agent_name} v{p.base_version}
            </span>
            <span className="min-w-0 flex-1 text-[13px] text-ink-2">{p.gate?.reasons.join('; ')}</span>
            <LinkButton href={`/traces/improve/${p.id}`} size="sm" variant="primary">
              Review
            </LinkButton>
          </li>
        ))}
      </ul>
    </Card>
  );
}

export default function ApprovalsPage(): JSX.Element {
  const { data, isLoading, error, refetch } = useApprovalInbox();
  const qc = useQueryClient();
  const now = useNow();
  const items = data ?? [];

  return (
    <AppShell>
      <PageBody>
        <PageHeader
          title="Approvals"
          description="Runs paused on a question for you, and changes to your agents waiting for a yes. An unanswered run question is decided by its agent's policy when it times out."
        />
        <ChangesToApprove />
        <Card tone={items.length ? 'accent' : 'default'} aria-label="Waiting approvals">
          {isLoading ? (
            <SkeletonRows rows={3} />
          ) : error ? (
            <div className="p-4">
              <ErrorNote message={getApiErrorMessage(error, 'Could not load approvals')} onRetry={() => void refetch()} />
            </div>
          ) : items.length === 0 ? (
            <EmptyState
              icon={<ShieldCheck size={28} />}
              title="No runs are waiting on you"
              description="When a run needs your approval to use a tool, it shows up here and on the session."
            />
          ) : (
            <ul>
              {items.map((item) => (
                <Row key={item.approval_id} item={item} now={now} onAnswered={() => void qc.invalidateQueries({ queryKey: operateKeys.inbox() })} />
              ))}
            </ul>
          )}
        </Card>
      </PageBody>
    </AppShell>
  );
}
