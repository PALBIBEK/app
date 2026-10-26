/**
 * Runners: the user's own machines that run sandbox commands (provider "runner").
 * Registering one shows its token once; re-registering replaces the token.
 */

'use client';

import { useState } from 'react';
import { Desktop, Trash } from '@phosphor-icons/react';
import { toast } from 'sonner';

import { AppShell, PageBody } from '@/components/layout/AppShell';
import { Button, Card, CardHeader, EmptyState, ErrorNote, Mono, PageHeader, SkeletonRows, StatusPill, Table, Td, Th, Tr } from '@/components/ui';
import { getApiErrorMessage } from '@/lib/api/errors';
import { useRegisterRunner, useRemoveRunner, useRunners } from '@/lib/api/hooks/useOperate';
import type { RunnerToken } from '@/lib/api/types/operate.types';
import { useAuthStore } from '@/lib/store/authStore';

const NAME = /^[a-z][a-z0-9_-]{0,63}$/;

export default function RunnersPage(): JSX.Element {
  const { data, isLoading, error, refetch } = useRunners();
  const register = useRegisterRunner();
  const remove = useRemoveRunner();
  const [name, setName] = useState('');
  const [issued, setIssued] = useState<RunnerToken | null>(null);
  const tenant = useAuthStore((s) => s.user?.id);
  const runners = data ?? [];
  const valid = NAME.test(name);

  async function add(e: React.FormEvent, runner = name): Promise<void> {
    e.preventDefault();
    if (!NAME.test(runner)) return;
    if (runners.some((r) => r.name === runner) && !window.confirm(`Replace ${runner}'s token? The old one stops working.`)) return;
    try {
      setIssued(await register.mutateAsync(runner));
      setName('');
    } catch (err) {
      toast.error(getApiErrorMessage(err, 'Could not register the runner'));
    }
  }

  async function drop(runner: string): Promise<void> {
    if (!window.confirm(`Remove ${runner}? Sandboxes that use it stop working until it is registered again.`)) return;
    try {
      await remove.mutateAsync(runner);
      if (issued?.name === runner) setIssued(null);
    } catch (err) {
      toast.error(getApiErrorMessage(err, 'Could not remove the runner'));
    }
  }

  return (
    <AppShell>
      <PageBody>
        <PageHeader
          title="Runners"
          description="Your own machines, running sandbox commands for agents whose sandbox provider is “runner”. A runner polls for work; nothing connects to it."
        />

        {issued ? (
          <Card tone="accent" aria-label="New runner token">
            <CardHeader tone="accent" title={`Token for ${issued.name}`} description="Shown once. Start the runner with it on the machine; registering again replaces it. The runner opens no port: it polls for work." />
            <div className="flex flex-col gap-3 p-4">
              <div className="rounded-lg border border-line-soft bg-canvas px-3 py-2">
                <Mono copy={issued.token} className="break-all text-xs" title="Runner token">
                  {issued.token}
                </Mono>
              </div>
              <pre className="overflow-x-auto rounded-lg bg-ink px-3.5 py-3 font-mono text-[12.5px] leading-[1.7] text-canvas">
                {`python -m supervisor.runner --url <supervisor URL> \\
    --tenant ${tenant ?? '<your id>'} --name ${issued.name} --token <the token> --root /srv/hands`}
              </pre>
              <div>
                <Button size="sm" onClick={() => setIssued(null)}>
                  I have saved it
                </Button>
              </div>
            </div>
          </Card>
        ) : null}

        <div className="flex flex-wrap items-start gap-5">
          <Card aria-label="Runners" className="min-w-0 flex-[999_1_560px]">
            {isLoading ? (
              <SkeletonRows rows={3} />
            ) : error ? (
              <div className="p-4">
                <ErrorNote message={getApiErrorMessage(error, 'Could not load runners')} onRetry={() => void refetch()} />
              </div>
            ) : runners.length === 0 ? (
              <EmptyState icon={<Desktop size={28} />} title="No runners yet" description="Register one to run an agent's commands on a machine you control." />
            ) : (
              <Table minWidth={480} label="Runners">
                <thead>
                  <tr>
                    <Th>Runner</Th>
                    <Th>State</Th>
                    <Th align="right">
                      <span className="sr-only">Actions</span>
                    </Th>
                  </tr>
                </thead>
                <tbody>
                  {runners.map((r) => (
                    <Tr key={r.name}>
                      <Td>
                        <Mono className="text-[13px] font-medium text-ink">{r.name}</Mono>
                      </Td>
                      <Td>
                        <StatusPill kind={r.online ? 'running' : 'stopped'} label={r.online ? 'Online' : 'Offline'} />
                      </Td>
                      <Td align="right">
                        <div className="flex justify-end gap-2">
                          <Button size="sm" variant="ghost" onClick={(e) => void add(e, r.name)} disabled={register.isPending}>
                            New token
                          </Button>
                          <Button size="sm" variant="danger" onClick={() => void drop(r.name)} disabled={remove.isPending} aria-label={`Remove ${r.name}`}>
                            <Trash size={14} />
                          </Button>
                        </div>
                      </Td>
                    </Tr>
                  ))}
                </tbody>
              </Table>
            )}
          </Card>

          <Card as="aside" className="flex min-w-0 flex-[1_1_300px] flex-col gap-3 p-5 xl:max-w-[400px]">
            <h2 className="text-sm font-semibold">Register a runner</h2>
            <form onSubmit={(e) => void add(e)} className="flex flex-col gap-2">
              <label htmlFor="runner-name" className="text-[12.5px] text-ink-2">
                Name
              </label>
              <input
                id="runner-name"
                value={name}
                onChange={(e) => setName(e.target.value.trim())}
                placeholder="e.g. build-box"
                className="h-10 rounded-lg border border-line-strong bg-surface px-3 font-mono text-[13px]"
                aria-invalid={name !== '' && !valid}
              />
              <p className="text-xs text-ink-3">Lowercase letters, digits, “-” and “_”, starting with a letter.</p>
              <Button type="submit" variant="primary" disabled={!valid || register.isPending}>
                {register.isPending ? 'Registering…' : 'Register'}
              </Button>
            </form>
            <p className="border-t border-line-soft pt-3 text-[13px] text-ink-2">
              Then pick the provider <Mono>runner</Mono> and this name in an environment or an agent&apos;s sandbox.
            </p>
          </Card>
        </div>
      </PageBody>
    </AppShell>
  );
}
