/**
 * Files: the workspaces the user's sessions work in, each a versioned file tree shared
 * between the user and the agent.
 */

'use client';

import Link from 'next/link';
import { useMemo, useState } from 'react';
import { FolderOpen } from '@phosphor-icons/react';

import { AppShell, PageBody } from '@/components/layout/AppShell';
import { WorkspacePanel } from '@/components/session/WorkspacePanel';
import { Card, EmptyState, Mono, PageHeader, SkeletonRows } from '@/components/ui';
import { useHarnesses } from '@/lib/api/hooks/useHarnesses';
import { useSessions } from '@/lib/api/hooks/useSessions';
import { cn, formatRelativeTime } from '@/lib/utils';

interface Space {
  scopeId: string;
  sessions: number;
  lastUsed: string;
  latestSession: string;
  agent: string | null;
}

export default function FilesPage(): JSX.Element {
  const { data, isLoading } = useSessions({ page: 1, page_size: 100 });
  const { data: harnesses } = useHarnesses();
  const [picked, setPicked] = useState<string | null>(null);

  const spaces = useMemo(() => {
    const names = new Map((harnesses?.data ?? []).map((h) => [h.id, h.name]));
    const m = new Map<string, Space>();
    for (const s of data?.data ?? []) {
      if (!s.scope_id) continue;
      const cur = m.get(s.scope_id);
      if (!cur) m.set(s.scope_id, { scopeId: s.scope_id, sessions: 1, lastUsed: s.updated_at, latestSession: s.id, agent: names.get(s.harness_id) ?? null });
      else {
        cur.sessions += 1;
        if (s.updated_at > cur.lastUsed) Object.assign(cur, { lastUsed: s.updated_at, latestSession: s.id });
      }
    }
    return [...m.values()].sort((a, b) => b.lastUsed.localeCompare(a.lastUsed));
  }, [data, harnesses]);

  const selected = spaces.find((s) => s.scopeId === picked) ?? spaces[0] ?? null;

  return (
    <AppShell>
      <PageBody>
        <PageHeader
          title="Files"
          description="Each workspace is a versioned file tree that you and your agents share. Every change keeps the earlier versions."
        />
        <div className="flex flex-wrap items-start gap-5">
          <Card as="aside" aria-label="Workspaces" className="min-w-0 flex-[1_1_260px] p-2.5 lg:max-w-[320px]">
            <p className="px-2.5 pb-1.5 pt-1 text-xs text-ink-3">Workspaces</p>
            {isLoading ? (
              <SkeletonRows rows={3} />
            ) : spaces.length === 0 ? (
              <p className="px-2.5 py-3 text-[13px] text-ink-2">None yet: a session started from an agent gets one.</p>
            ) : (
              <ul className="flex flex-col gap-0.5">
                {spaces.map((s) => {
                  const on = s.scopeId === selected?.scopeId;
                  return (
                    <li key={s.scopeId}>
                      <button
                        type="button"
                        onClick={() => setPicked(s.scopeId)}
                        aria-current={on || undefined}
                        className={cn('flex w-full flex-col rounded-lg px-2.5 py-2 text-left', on ? 'bg-accent-50 text-accent-700' : 'hover:bg-canvas')}
                      >
                        <span className={cn('truncate font-mono text-[12.5px]', on ? 'font-medium' : 'text-ink')}>{s.scopeId}</span>
                        <span className="text-xs text-ink-3">
                          {s.agent ?? 'agent'} · {s.sessions} session{s.sessions === 1 ? '' : 's'} · {formatRelativeTime(s.lastUsed)}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </Card>
          <div className="flex min-w-0 flex-[999_1_560px] flex-col gap-3">
            {selected ? (
              <>
                <div className="flex flex-wrap items-center gap-3 text-[13px] text-ink-2">
                  <FolderOpen size={16} aria-hidden="true" />
                  <Mono className="text-ink">{selected.scopeId}</Mono>
                  <span className="flex-1" />
                  <Link href={`/sessions/${selected.latestSession}`} className="font-medium text-accent-700 hover:text-accent-600">
                    Open its latest session
                  </Link>
                </div>
                <Card>
                  <WorkspacePanel key={selected.scopeId} scopeId={selected.scopeId} sessionId={selected.latestSession} className="p-5 text-sm" />
                </Card>
              </>
            ) : !isLoading ? (
              <Card>
                <EmptyState icon={<FolderOpen size={28} />} title="No workspaces yet" description="Start a session on an agent: its workspace appears here." />
              </Card>
            ) : null}
          </div>
        </div>
      </PageBody>
    </AppShell>
  );
}
