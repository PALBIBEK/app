'use client';

/**
 * Model providers: the vendors whose models sessions can run (Anthropic, OpenAI, …, and a
 * local server), on Policies & secrets. An owner or admin adds a vendor's key, which the
 * vendor checks before it is kept and which this page never shows again; or removes it, and
 * that vendor's models are off. A local server is turned on by its address (Ollama, vLLM,
 * LM Studio), with an optional key, and lists the models it runs. Members see nothing here:
 * the session's model picker tells them who can add a provider.
 */

import { useState } from 'react';
import { CheckCircle, Circle, Cpu, Key, Trash, WarningCircle } from '@phosphor-icons/react';
import { toast } from 'sonner';

import { Button, Card, CardHeader, ErrorNote, SkeletonRows } from '@/components/ui';
import { getApiErrorMessage } from '@/lib/api/errors';
import { useModelProviders, useRemoveProviderKey, useSetProviderAddress, useSetProviderKey } from '@/lib/api/hooks/useOperate';
import type { ModelProvider } from '@/lib/api/types/provider.types';
import { useAuthStore } from '@/lib/store/authStore';
import { cn } from '@/lib/utils';

export const MODEL_PROVIDERS_ID = 'model-providers';

/** Whether the signed-in person may change the platform's model providers. */
export function useCanManageProviders(): boolean {
  const role = useAuthStore((s) => s.user?.role);
  return role === 'owner' || role === 'admin';
}

/** What a vendor's state is, in words. */
export function providerState(p: ModelProvider): { ready: boolean; text: string } {
  if (p.address_settable) {
    if (!p.base_url) return { ready: false, text: 'Not set up: add your server’s address' };
    if (p.unreachable) return { ready: false, text: `Not answering: ${p.unreachable}` };
    const n = p.models?.length ?? 0;
    return n ? { ready: p.ready, text: `${n} model${n === 1 ? '' : 's'} found` } : { ready: false, text: 'Answering, but it lists no models' };
  }
  if (p.source === 'set') return { ready: p.ready, text: 'Key added here' };
  if (p.source === 'environment') return { ready: p.ready, text: 'Key set by the deployment' };
  if (p.source === 'removed') return { ready: false, text: 'Key removed: its models are off' };
  return { ready: false, text: 'No key' };
}

function KeyForm({ provider, editable }: { provider: ModelProvider; editable: boolean }): JSX.Element {
  const [key, setKey] = useState('');
  const set = useSetProviderKey();
  const remove = useRemoveProviderKey();
  const has = provider.source === 'set' || provider.source === 'environment';

  async function save(e: React.FormEvent): Promise<void> {
    e.preventDefault();
    if (!key.trim()) return;
    try {
      const saved = await set.mutateAsync({ vendor: provider.id, key: key.trim() });
      toast.success(provider.key_checked ? `${provider.label} accepted the key` : `Saved the ${provider.label} key (${provider.label} offers no check)`);
      setKey('');
      if (!saved.ready) toast.info(`${provider.label} has the key, but is not ready yet`);
    } catch (err) {
      toast.error(getApiErrorMessage(err, `Could not save the ${provider.label} key`));
    }
  }

  async function drop(): Promise<void> {
    if (!window.confirm(`Remove the ${provider.label} key? Its models are off for every session until a key is added again.`)) return;
    try {
      await remove.mutateAsync(provider.id);
    } catch (err) {
      toast.error(getApiErrorMessage(err, `Could not remove the ${provider.label} key`));
    }
  }

  if (!editable) return <p className="text-[12.5px] text-ink-3">Keys are set in the deployment’s environment here.</p>;
  return (
    <form onSubmit={(e) => void save(e)} className="flex flex-wrap items-center gap-2">
      <input
        type="password"
        value={key}
        onChange={(e) => setKey(e.target.value)}
        placeholder={has ? 'Replace the key' : provider.address_settable ? 'Key (optional)' : 'Paste an API key'}
        aria-label={`${provider.label} API key`}
        autoComplete="new-password"
        className="h-9 min-w-0 flex-1 rounded-lg border border-line-strong bg-surface px-3 font-mono text-[13px]"
      />
      <Button type="submit" size="sm" variant="primary" disabled={!key.trim() || set.isPending}>
        {set.isPending ? (provider.key_checked ? 'Checking…' : 'Saving…') : has ? 'Replace' : 'Add key'}
      </Button>
      {provider.source === 'set' || provider.source === 'environment' ? (
        <Button size="sm" variant="danger" onClick={() => void drop()} disabled={remove.isPending} aria-label={`Remove the ${provider.label} key`}>
          <Trash size={14} />
        </Button>
      ) : null}
    </form>
  );
}

function AddressForm({ provider, editable }: { provider: ModelProvider; editable: boolean }): JSX.Element {
  const [address, setAddress] = useState(provider.base_url ?? '');
  const set = useSetProviderAddress();
  const unchanged = !!provider.base_url && address.trim() === provider.base_url;

  async function save(e: React.FormEvent): Promise<void> {
    e.preventDefault();
    try {
      const saved = await set.mutateAsync({ vendor: provider.id, baseUrl: address.trim() });
      const n = saved.models?.length ?? 0;
      if (!address.trim()) toast.success('Local models are off');
      else if (saved.unreachable) toast.error(`Saved, but the server is not answering: ${saved.unreachable}`);
      else toast.success(`Found ${n} model${n === 1 ? '' : 's'}`);
    } catch (err) {
      toast.error(getApiErrorMessage(err, 'Could not set the address'));
    }
  }

  if (!editable) return <p className="text-[12.5px] text-ink-3">The address is set in the deployment’s environment here.</p>;
  return (
    <form onSubmit={(e) => void save(e)} className="flex flex-wrap items-center gap-2">
      <input
        value={address}
        onChange={(e) => setAddress(e.target.value)}
        placeholder="http://localhost:11434/v1 (Ollama)"
        aria-label="Local server address"
        className="h-9 min-w-0 flex-1 rounded-lg border border-line-strong bg-surface px-3 font-mono text-[13px]"
      />
      <Button type="submit" size="sm" variant="primary" disabled={set.isPending || (!address.trim() && !provider.base_url)}>
        {set.isPending ? 'Connecting…' : unchanged ? 'Check again' : 'Save and connect'}
      </Button>
    </form>
  );
}

function ProviderRow({ provider, editable }: { provider: ModelProvider; editable: boolean }): JSX.Element {
  const state = providerState(provider);
  return (
    <li data-testid={`provider-${provider.id}`} className="flex flex-col gap-2.5 px-4 py-3.5">
      <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
        {provider.address_settable ? <Cpu size={16} className="text-ink-2" aria-hidden="true" /> : <Key size={16} className="text-ink-2" aria-hidden="true" />}
        <span className="font-medium">{provider.label}</span>
        <span data-testid="provider-state" className={cn('inline-flex items-center gap-1 text-[12.5px]', state.ready ? 'text-good' : 'text-ink-3')}>
          {state.ready ? (
            <CheckCircle size={13} weight="fill" aria-hidden="true" />
          ) : provider.unreachable ? (
            <WarningCircle size={13} weight="fill" className="text-warn" aria-hidden="true" />
          ) : (
            <Circle size={13} aria-hidden="true" />
          )}
          {state.text}
        </span>
      </div>
      {provider.address_settable ? (
        <>
          <AddressForm provider={provider} editable={editable} />
          {provider.models?.length ? (
            <p className="text-[12.5px] text-ink-2">
              Runs: <span className="font-mono">{provider.models.slice(0, 8).join(', ')}{provider.models.length > 8 ? ` and ${provider.models.length - 8} more` : ''}</span>
            </p>
          ) : null}
          {provider.base_url ? <KeyForm provider={provider} editable={editable} /> : null}
        </>
      ) : (
        <KeyForm provider={provider} editable={editable} />
      )}
    </li>
  );
}

export function ModelProviders(): JSX.Element | null {
  const canManage = useCanManageProviders();
  const { data, isLoading, error, refetch } = useModelProviders(canManage);
  if (!canManage) return null;
  const vendors = data ? [...data.vendors.filter((v) => !v.address_settable), ...data.vendors.filter((v) => v.address_settable)] : [];
  return (
    <Card aria-labelledby={`${MODEL_PROVIDERS_ID}-title`} id={MODEL_PROVIDERS_ID} className="scroll-mt-6">
      <CardHeader
        title={<span id={`${MODEL_PROVIDERS_ID}-title`}>Model providers</span>}
        description="The models sessions can pick from. A key is checked with its provider before it is kept, and never shown again; removing it turns that provider’s models off for every session."
      />
      {isLoading ? (
        <SkeletonRows rows={4} />
      ) : error ? (
        <div className="p-4">
          <ErrorNote message={getApiErrorMessage(error, 'Could not load the model providers')} onRetry={() => void refetch()} />
        </div>
      ) : (
        <ul className="divide-y divide-line-soft" data-testid="model-providers">
          {vendors.map((p) => (
            <ProviderRow key={p.id} provider={p} editable={data?.editable ?? false} />
          ))}
        </ul>
      )}
    </Card>
  );
}
