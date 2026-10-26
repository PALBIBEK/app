/**
 * Agents: every agent the user has built, what version is live, and (for the one picked)
 * its settings and version history, with a way to start a session on it.
 */

'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import { Plus, X } from '@phosphor-icons/react';

import { AppShell, PageBody } from '@/components/layout/AppShell';
import { AiPlatformBanner } from '@/components/platform/AiPlatformBanner';
import {
  Card,
  EmptyState,
  ErrorNote,
  LinkButton,
  Mono,
  PageHeader,
  SearchInput,
  Segmented,
  SkeletonRows,
  Table,
  Td,
  Th,
  Tr,
  shortHash,
} from '@/components/ui';
import { useHarnesses } from '@/lib/api/hooks/useHarnesses';
import { useReleases } from '@/lib/api/hooks/useManifest';
import { usePlatformStatus } from '@/lib/api/hooks/usePlatformStatus';
import { useSessions } from '@/lib/api/hooks/useSessions';
import type { Harness } from '@/lib/api/types/harness.types';
import { cn, formatMoney, formatRelativeTime, formatStamp, versionLabel } from '@/lib/utils';

type Filter = 'all' | 'live' | 'draft' | 'stale';

function stateOf(h: Harness): Exclude<Filter, 'all'> {
  const s = h.bind?.bind_status ?? 'unbound';
  if (s === 'bound') return 'live';
  if (s === 'stale' || s === 'error') return 'stale';
  return 'draft';
}

const STATE_LABEL: Record<Exclude<Filter, 'all'>, string> = { live: 'Published', draft: 'Draft', stale: 'Needs republish' };

function StateMark({ state }: { state: Exclude<Filter, 'all'> }): JSX.Element {
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-[13px]">
      {state === 'live' ? (
        <span className="h-2 w-2 rounded-full bg-ink" aria-hidden="true" />
      ) : state === 'draft' ? (
        <span className="box-border h-2 w-2 rounded-full border-[1.5px] border-ink" aria-hidden="true" />
      ) : (
        <span className="h-2 w-2 rotate-45 bg-accent-500" aria-hidden="true" />
      )}
      {STATE_LABEL[state]}
    </span>
  );
}

export default function AgentsPage(): JSX.Element {
  const { data, isLoading, error, refetch } = useHarnesses();
  const { data: sessions } = useSessions({ page: 1, page_size: 100 });
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  const [picked, setPicked] = useState<string | null>(null);

  const agents = useMemo(() => data?.data ?? [], [data]);
  const liveCount = useMemo(() => {
    const m = new Map<string, number>();
    for (const s of sessions?.data ?? []) if (s.status === 'active' || s.status === 'awaiting_approval') m.set(s.harness_id, (m.get(s.harness_id) ?? 0) + 1);
    return m;
  }, [sessions]);

  const counts = useMemo(() => {
    const c = { all: agents.length, live: 0, draft: 0, stale: 0 };
    for (const a of agents) c[stateOf(a)] += 1;
    return c;
  }, [agents]);

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase().replace(/^sha256:/, '');
    return agents.filter((a) => {
      if (filter !== 'all' && stateOf(a) !== filter) return false;
      if (!q) return true;
      return [a.name, a.description ?? '', a.bind?.composition_digest ?? '', a.model].some((v) => v.toLowerCase().includes(q));
    });
  }, [agents, query, filter]);

  // Pick the first agent once they load (on wide screens the detail is always beside the list).
  useEffect(() => {
    if (picked && agents.some((a) => a.id === picked)) return;
    if (agents.length && typeof window !== 'undefined' && window.matchMedia('(min-width: 1280px)').matches) setPicked(agents[0].id);
  }, [agents, picked]);

  const selected = agents.find((a) => a.id === picked) ?? null;

  return (
    <AppShell>
      <PageBody>
        <AiPlatformBanner />
        <PageHeader
          title="Agents"
          description="Each published version is fixed and addressed by its content hash; sessions keep the version they started on."
          actions={
            <LinkButton href="/harness/new" variant="primary">
              <Plus size={16} /> New agent
            </LinkButton>
          }
        />

        <div className="flex flex-wrap items-center gap-x-4 gap-y-2.5">
          <SearchInput value={query} onChange={setQuery} label="Search agents or hashes" placeholder="Search by name, model or sha256:…" />
          <Segmented
            label="Filter by status"
            value={filter}
            onChange={setFilter}
            options={[
              { value: 'all', label: 'All', count: counts.all },
              { value: 'live', label: 'Published', count: counts.live },
              { value: 'draft', label: 'Draft', count: counts.draft },
              { value: 'stale', label: 'Needs republish', count: counts.stale },
            ]}
          />
        </div>

        <div className="flex flex-wrap items-start gap-5">
          <Card aria-label="Agent list" className="min-w-0 flex-[999_1_620px]">
            {isLoading ? (
              <SkeletonRows rows={6} />
            ) : error ? (
              <div className="p-4">
                <ErrorNote message={error instanceof Error ? error.message : 'Could not load agents'} onRetry={() => void refetch()} />
              </div>
            ) : agents.length === 0 ? (
              <EmptyState
                title="No agents yet"
                description="An agent is a prompt, a model, tools, a sandbox and a policy. Build one, publish it, then start sessions on it."
                action={
                  <LinkButton href="/harness/new" variant="primary">
                    Create your first agent
                  </LinkButton>
                }
              />
            ) : rows.length === 0 ? (
              <EmptyState title="Nothing matches" description="Try another status or search." />
            ) : (
              <Table minWidth={560} label="Agents">
                <thead>
                  <tr>
                    <Th>Agent</Th>
                    <Th>Version</Th>
                    <Th className="hidden 2xl:table-cell">Content hash</Th>
                    <Th>Status</Th>
                    <Th align="right">Live</Th>
                    <Th>Updated</Th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((a) => {
                    const on = a.id === picked;
                    return (
                      <Tr key={a.id} className={cn('cursor-pointer', on && 'bg-accent-50 shadow-[inset_3px_0_0_theme(colors.accent.500)] hover:bg-accent-50')}>
                        <Td>
                          <button type="button" onClick={() => setPicked(a.id)} aria-pressed={on} className="block text-left" data-testid="agent-row">
                            <span className="font-medium text-ink">{a.name}</span>
                            <span className="block max-w-[300px] truncate text-[12.5px] text-ink-3">{a.description || a.model}</span>
                          </button>
                        </Td>
                        <Td mono>{versionLabel(a.bind?.harness_version)}</Td>
                        <Td mono className="hidden text-ink-2 2xl:table-cell">
                          {a.bind?.composition_digest ? `sha256:${shortHash(a.bind.composition_digest)}` : '—'}
                        </Td>
                        <Td>
                          <StateMark state={stateOf(a)} />
                        </Td>
                        <Td mono align="right">
                          {liveCount.get(a.id) ?? 0}
                        </Td>
                        <Td className="whitespace-nowrap text-ink-2">{formatRelativeTime(a.updated_at)}</Td>
                      </Tr>
                    );
                  })}
                </tbody>
              </Table>
            )}
          </Card>

          {selected ? <AgentDetail agent={selected} onClose={() => setPicked(null)} live={liveCount.get(selected.id) ?? 0} /> : null}
        </div>
      </PageBody>
    </AppShell>
  );
}

const SANDBOX_LABEL: Record<string, string> = {
  none: 'No sandbox',
  local_process: 'Local process',
  local_docker: 'Docker',
  e2b: 'E2B',
};

function AgentDetail({ agent, onClose, live }: { agent: Harness; onClose: () => void; live: number }): JSX.Element {
  const { data: releases, isLoading } = useReleases(agent.id);
  const { data: platform } = usePlatformStatus();
  const current = releases?.[0];
  const digest = current?.composition_digest ?? agent.bind?.composition_digest ?? null;
  const published = stateOf(agent) !== 'draft';
  const canRun = published && platform?.run_allowed !== false;
  const m = current?.manifest;

  return (
    <Card as="aside" aria-label="Agent detail" className="flex min-w-0 flex-[1_1_340px] flex-col xl:max-w-[420px]">
      <div className="flex flex-col gap-2.5 border-b border-line-soft px-5 py-[18px]">
        <div className="flex items-center gap-2.5">
          <h2 className="min-w-0 truncate text-lg font-semibold tracking-[-0.01em]">{agent.name}</h2>
          {current ? <span className="rounded-md bg-accent-50 px-2 py-0.5 font-mono text-xs text-accent-700">v{current.version}</span> : null}
          <span className="flex-1" />
          <button type="button" aria-label="Close detail" onClick={onClose} className="flex h-8 w-8 items-center justify-center rounded-md text-ink-2 hover:bg-line-soft">
            <X size={16} />
          </button>
        </div>
        {agent.description ? <p className="text-ink-2">{agent.description}</p> : null}
        {digest ? (
          <div className="rounded-lg border border-line-soft bg-canvas px-2.5 py-1.5">
            <Mono copy={digest} className="break-all text-xs">
              sha256:{digest.replace(/^sha256:/, '')}
            </Mono>
          </div>
        ) : null}
        <div className="flex flex-wrap gap-2">
          {canRun ? (
            <LinkButton href={`/harness/${agent.id}/test`} variant="primary">
              Start session
            </LinkButton>
          ) : null}
          <LinkButton href={`/harness/${agent.id}`}>{published ? 'Open builder' : 'Finish and publish'}</LinkButton>
          <LinkButton href={`/harness/${agent.id}/sessions`}>Sessions{live ? ` · ${live} live` : ''}</LinkButton>
        </div>
        {!canRun && published ? <p className="text-xs text-ink-3">The AI platform is not ready, so sessions cannot start right now.</p> : null}
      </div>

      {m ? (
        <dl className="grid grid-cols-2 gap-x-4 gap-y-3.5 border-b border-line-soft px-5 py-4">
          <div>
            <dt className="text-xs text-ink-3">Model</dt>
            <dd className="mt-0.5 font-mono text-[12.5px]">{m.model}</dd>
          </div>
          <div>
            <dt className="text-xs text-ink-3">Sandbox</dt>
            <dd className="mt-0.5 font-mono text-[12.5px]">
              {SANDBOX_LABEL[m.sandbox.provider] ?? m.sandbox.provider} · {m.sandbox.file_effects.replace(/_/g, ' ')}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-ink-3">Budget per session</dt>
            <dd className="mt-0.5 font-mono text-[12.5px]">
              {m.policy.max_cost_per_session ? formatMoney(m.policy.max_cost_per_session) : 'no cap'} · {m.policy.max_steps} steps
            </dd>
          </div>
          <div>
            <dt className="text-xs text-ink-3">Unanswered approvals</dt>
            <dd className="mt-0.5 font-mono text-[12.5px]">
              {m.policy.on_approval_timeout} after {Math.round(m.policy.approval_timeout_s / 60) || 1}m
            </dd>
          </div>
          <div className="col-span-2">
            <dt className="text-xs text-ink-3">Tools</dt>
            <dd className="mt-1.5 flex flex-wrap gap-1.5">
              {m.tools.length ? (
                m.tools.map((t) => (
                  <Mono key={t} chip className="text-xs">
                    {t}
                  </Mono>
                ))
              ) : (
                <span className="text-[13px] text-ink-2">None</span>
              )}
            </dd>
          </div>
          <div className="col-span-2">
            <dt className="text-xs text-ink-3">Requires approval</dt>
            <dd className="mt-0.5 text-[13px]">
              {m.policy.require_approval.length ? (
                m.policy.require_approval.map((t, i) => (
                  <span key={t}>
                    {i ? ', ' : ''}
                    <span className="font-mono text-[12.5px]">{t}</span>
                  </span>
                ))
              ) : m.sandbox.approval_default === 'ask' ? (
                'Every sandbox action asks first'
              ) : (
                'Nothing'
              )}
            </dd>
          </div>
        </dl>
      ) : null}

      <div className="flex flex-col gap-3 px-5 pb-5 pt-4">
        <div className="flex items-baseline justify-between">
          <h3 className="text-sm font-semibold">Version history</h3>
          {releases && releases.length > 4 ? (
            <Link href={`/harness/${agent.id}`} className="text-[12.5px] text-ink-2 hover:text-ink">
              All {releases.length} versions
            </Link>
          ) : null}
        </div>
        {isLoading ? (
          <SkeletonRows rows={3} />
        ) : !releases?.length ? (
          <p className="text-[13px] text-ink-2">Not published yet. Publishing fixes a version that sessions can run.</p>
        ) : (
          <ol className="flex flex-col">
            {releases.slice(0, 4).map((r, i) => (
              <li key={r.version} className="flex gap-3 border-t border-line-soft py-2.5">
                <div className="flex-none pt-[5px]">
                  <span
                    aria-hidden="true"
                    className={cn('block h-2.5 w-2.5 rounded-full', i === 0 ? 'bg-accent-500' : 'box-border border-[1.5px] border-[#8A9099]')}
                  />
                </div>
                <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
                    <span className="font-mono text-[13px] font-medium">v{r.version}</span>
                    <span className="font-mono text-xs text-ink-2">{shortHash(r.composition_digest)}</span>
                    {i === 0 ? <span className="rounded-full bg-accent-50 px-[7px] py-px text-[11.5px] text-accent-700">live</span> : null}
                  </div>
                  <div className="text-[13px]">
                    {r.manifest.model} · {r.manifest.tools.length} tool{r.manifest.tools.length === 1 ? '' : 's'} · {r.manifest.policy.max_steps} steps
                  </div>
                  <div className="text-xs text-ink-3">{formatStamp(r.created_at)}</div>
                </div>
              </li>
            ))}
          </ol>
        )}
      </div>
    </Card>
  );
}
