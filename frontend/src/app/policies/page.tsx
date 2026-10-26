/**
 * Policies & secrets: what each agent may do without asking, what it must ask for, its
 * limits; and the secrets its tools can use (names only: a value is never shown again).
 */

'use client';

import Link from 'next/link';
import { useState } from 'react';
import { useQueries } from '@tanstack/react-query';
import { Key, Trash } from '@phosphor-icons/react';
import { toast } from 'sonner';

import { AppShell, PageBody } from '@/components/layout/AppShell';
import { Button, Card, CardHeader, EmptyState, ErrorNote, LinkButton, Mono, PageHeader, SkeletonRows, Table, Td, Th, Tr } from '@/components/ui';
import { harnessesAPI } from '@/lib/api/endpoints/harnesses';
import { getApiErrorMessage } from '@/lib/api/errors';
import { useHarnesses } from '@/lib/api/hooks/useHarnesses';
import { manifestKeys } from '@/lib/api/hooks/useManifest';
import { useDeleteSecret, usePutSecret, useSecretNames } from '@/lib/api/hooks/useOperate';
import { formatMoney } from '@/lib/utils';

const SECRET = /^[A-Za-z0-9_][A-Za-z0-9_./-]{0,127}$/;

function Decision({ value }: { value: 'Allow' | 'Ask' | 'Deny' }): JSX.Element {
  if (value === 'Ask') return <span className="rounded-full bg-accent-50 px-2 py-px text-[12.5px] font-medium text-accent-700">Ask</span>;
  if (value === 'Deny') return <span className="text-[13px] font-medium">Deny</span>;
  return <span className="text-[13px] text-ink-2">Allow</span>;
}

function AgentPolicies(): JSX.Element {
  const { data, isLoading } = useHarnesses();
  const agents = (data?.data ?? []).filter((h) => h.bind?.bind_status === 'bound' || h.bind?.bind_status === 'stale');
  const releases = useQueries({
    queries: agents.map((a) => ({ queryKey: manifestKeys.releases(a.id), queryFn: () => harnessesAPI.releases(a.id) })),
  });

  return (
    <Card aria-labelledby="agent-policies">
      <CardHeader
        title={<span id="agent-policies">Agent policies</span>}
        description="Each published version carries its own policy; change it on the agent's Policy tab and publish. Running sessions keep theirs."
      />
      {isLoading ? (
        <SkeletonRows rows={3} />
      ) : agents.length === 0 ? (
        <EmptyState title="No published agents" description="A policy applies once an agent is published." />
      ) : (
        <Table minWidth={820} label="Agent policies">
          <thead>
            <tr>
              <Th>Agent</Th>
              <Th>Sandbox actions</Th>
              <Th>Asks before</Th>
              <Th>Unanswered</Th>
              <Th align="right">Spend cap</Th>
              <Th align="right">Steps</Th>
              <Th>
                <span className="sr-only">Edit</span>
              </Th>
            </tr>
          </thead>
          <tbody>
            {agents.map((a, i) => {
              const m = releases[i]?.data?.[0]?.manifest;
              return (
                <Tr key={a.id}>
                  <Td>
                    <Link href={`/harness/${a.id}`} className="font-medium text-ink hover:text-accent-700">
                      {a.name}
                    </Link>
                    {releases[i]?.data?.[0] ? <span className="ml-2 font-mono text-xs text-ink-3">v{releases[i].data![0].version}</span> : null}
                  </Td>
                  <Td>{m ? <Decision value={m.sandbox.provider === 'none' ? 'Deny' : m.sandbox.approval_default === 'ask' ? 'Ask' : 'Allow'} /> : '…'}</Td>
                  <Td>
                    {m ? (
                      m.policy.require_approval.length ? (
                        <span className="flex flex-wrap gap-1">
                          {m.policy.require_approval.map((t) => (
                            <Mono key={t} chip className="text-xs">
                              {t}
                            </Mono>
                          ))}
                        </span>
                      ) : (
                        <span className="text-[13px] text-ink-2">nothing</span>
                      )
                    ) : null}
                  </Td>
                  <Td className="whitespace-nowrap text-[13px]">{m ? `${m.policy.on_approval_timeout} after ${Math.max(1, Math.round(m.policy.approval_timeout_s / 60))}m` : null}</Td>
                  <Td mono align="right">
                    {m ? (m.policy.max_cost_per_session ? formatMoney(m.policy.max_cost_per_session) : 'none') : null}
                  </Td>
                  <Td mono align="right">
                    {m?.policy.max_steps}
                  </Td>
                  <Td align="right">
                    <LinkButton href={`/harness/${a.id}`} size="sm" variant="ghost">
                      Edit
                    </LinkButton>
                  </Td>
                </Tr>
              );
            })}
          </tbody>
        </Table>
      )}
    </Card>
  );
}

function Secrets(): JSX.Element {
  const { data, isLoading, error, refetch } = useSecretNames();
  const put = usePutSecret();
  const del = useDeleteSecret();
  const [name, setName] = useState('');
  const [value, setValue] = useState('');
  const names = data ?? [];
  const valid = SECRET.test(name) && !name.includes('..') && value.length > 0;

  async function save(e: React.FormEvent): Promise<void> {
    e.preventDefault();
    if (!valid) return;
    if (names.includes(name) && !window.confirm(`Replace the value of ${name}?`)) return;
    try {
      await put.mutateAsync({ name, value });
      toast.success(`Saved ${name}`);
      setName('');
      setValue('');
    } catch (err) {
      toast.error(getApiErrorMessage(err, 'Could not save the secret'));
    }
  }

  async function remove(n: string): Promise<void> {
    if (!window.confirm(`Delete ${n}? Tools that use it fail until it is set again.`)) return;
    try {
      await del.mutateAsync(n);
    } catch (err) {
      toast.error(getApiErrorMessage(err, 'Could not delete the secret'));
    }
  }

  return (
    <Card aria-labelledby="secrets">
      <CardHeader title={<span id="secrets">Secrets</span>} description="Kept in the vault, encrypted. Tools read them by name; this page never shows a value again." />
      <div className="grid gap-0 lg:grid-cols-[1fr_340px]">
        <div className="min-w-0 border-line-soft lg:border-r">
          {isLoading ? (
            <SkeletonRows rows={3} />
          ) : error ? (
            <div className="p-4">
              <ErrorNote message={getApiErrorMessage(error, 'Could not load secrets')} onRetry={() => void refetch()} />
            </div>
          ) : names.length === 0 ? (
            <EmptyState icon={<Key size={26} />} title="No secrets yet" description="Add an API key or token a tool needs." />
          ) : (
            <ul className="divide-y divide-line-soft" data-testid="secret-names">
              {names.map((n) => (
                <li key={n} className="flex items-center gap-3 px-4 py-2.5">
                  <Key size={15} className="text-ink-3" aria-hidden="true" />
                  <Mono className="flex-1 text-[13px] text-ink">{n}</Mono>
                  <span className="font-mono text-xs text-ink-3">••••••••</span>
                  <Button size="sm" variant="ghost" onClick={() => setName(n)}>
                    Replace
                  </Button>
                  <Button size="sm" variant="danger" onClick={() => void remove(n)} aria-label={`Delete ${n}`} disabled={del.isPending}>
                    <Trash size={14} />
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </div>
        <form onSubmit={(e) => void save(e)} className="flex flex-col gap-2 border-t border-line-soft p-4 lg:border-t-0">
          <h3 className="text-sm font-semibold">{names.includes(name) ? `Replace ${name}` : 'Add a secret'}</h3>
          <label htmlFor="secret-name" className="text-[12.5px] text-ink-2">
            Name
          </label>
          <input
            id="secret-name"
            value={name}
            onChange={(e) => setName(e.target.value.trim())}
            placeholder="e.g. GITHUB_TOKEN"
            autoComplete="off"
            className="h-10 rounded-lg border border-line-strong bg-surface px-3 font-mono text-[13px]"
          />
          <label htmlFor="secret-value" className="text-[12.5px] text-ink-2">
            Value
          </label>
          <input
            id="secret-value"
            type="password"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            autoComplete="new-password"
            className="h-10 rounded-lg border border-line-strong bg-surface px-3 font-mono text-[13px]"
          />
          <Button type="submit" variant="primary" disabled={!valid || put.isPending}>
            {put.isPending ? 'Saving…' : 'Save secret'}
          </Button>
        </form>
      </div>
    </Card>
  );
}

export default function PoliciesPage(): JSX.Element {
  return (
    <AppShell>
      <PageBody>
        <PageHeader title="Policies & secrets" description="What agents may do without asking, what they must ask for, their limits, and the secrets their tools use." />
        <AgentPolicies />
        <Secrets />
      </PageBody>
    </AppShell>
  );
}
