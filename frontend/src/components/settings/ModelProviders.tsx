'use client';

/**
 * The platform's model vendors: whether each has a key and where it came from, and a key to
 * set or remove, as QM's provider settings do (``plugins/admin/ui/settings-providers.ts``).
 *
 * A key is typed once and sent to the model gateway, which checks it with the vendor before
 * keeping it (a vendor without a documented check keeps it unchecked, and says so). Nothing
 * here ever shows a key again: a status says only "set here", "from the server", and so on.
 */

import { useState } from 'react';
import { toast } from 'sonner';

import { Button } from '@/components/ui';
import {
  canManageModelProviders,
  useModelProviders,
  useRemoveModelProviderKey,
  useSetModelProviderKey,
} from '@/lib/api/hooks/useModelProviders';
import type { ModelProvider } from '@/lib/api/types/modelProvider.types';
import { useAuthStore } from '@/lib/store/authStore';
import { cn } from '@/lib/utils';

const SOURCE: Record<ModelProvider['source'], string> = {
  set: 'Key set here',
  environment: "Key from the server's environment",
  removed: 'Removed here',
  absent: 'No key',
};

/** What the vendor's badge says: whether its models can run, and why not. */
export function vendorStatus(v: ModelProvider): { label: string; ready: boolean } {
  if (v.ready) return { label: v.key_required ? SOURCE[v.source] : 'Ready', ready: true };
  if (!v.key_required && !v.base_url) return { label: 'Not set up', ready: false };
  return { label: SOURCE[v.source], ready: false };
}

function when(at: number | null | undefined): string {
  return at ? new Date(at * 1000).toLocaleString() : '';
}

function VendorCard({ vendor, editable }: { vendor: ModelProvider; editable: boolean }): JSX.Element {
  const [key, setKey] = useState('');
  const save = useSetModelProviderKey();
  const remove = useRemoveModelProviderKey();
  const status = vendorStatus(vendor);
  const busy = save.isPending || remove.isPending;
  const canRemove = editable && (vendor.source === 'set' || vendor.source === 'environment');
  return (
    <li className="flex flex-col gap-2.5 rounded-lg border border-line bg-surface p-4" data-testid={`vendor-${vendor.id}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium text-ink">{vendor.label}</span>
        <span
          className={cn(
            'rounded-full px-2 py-0.5 text-xs',
            status.ready ? 'bg-emerald-50 text-emerald-700' : 'bg-line-soft text-ink-2',
          )}
          data-testid="vendor-status"
        >
          {status.label}
        </span>
        <span className="flex-1" />
        <span className="truncate font-mono text-xs text-ink-3" title={vendor.base_url}>
          {vendor.base_url || 'no address'}
        </span>
      </div>
      {vendor.updated_by ? (
        <p className="text-xs text-ink-3">
          Changed {when(vendor.updated_at)} by {vendor.updated_by}
        </p>
      ) : null}
      {editable ? (
        <form
          className="flex flex-wrap items-center gap-2"
          aria-label={`${vendor.label} key`}
          onSubmit={async (e) => {
            e.preventDefault();
            if (!key.trim()) return;
            try {
              const saved = await save.mutateAsync({ vendor: vendor.id, key: key.trim() });
              setKey('');
              toast.success(
                saved.checked === false
                  ? `${vendor.label} key saved. ${vendor.label} has no way to check a key first: a wrong one shows when a model is called.`
                  : `${vendor.label} accepted the key. Its models can be picked now.`,
              );
            } catch (err) {
              toast.error(err instanceof Error ? err.message : 'The key was not saved');
            }
          }}
        >
          <input
            type="password"
            autoComplete="off"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder={vendor.key_required ? 'Paste an API key' : 'API key (optional)'}
            aria-label={`${vendor.label} API key`}
            className="h-9 min-w-0 flex-[1_1_240px] rounded-lg border border-line-strong bg-surface px-2.5 text-sm"
          />
          <Button type="submit" size="sm" variant="primary" disabled={busy || !key.trim()}>
            {vendor.key_checked ? 'Check and save' : 'Save'}
          </Button>
          {canRemove ? (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={busy}
              onClick={async () => {
                try {
                  await remove.mutateAsync(vendor.id);
                  toast.success(`${vendor.label} is off: its models can no longer be picked.`);
                } catch (err) {
                  toast.error(err instanceof Error ? err.message : 'The key was not removed');
                }
              }}
            >
              Remove
            </Button>
          ) : null}
        </form>
      ) : null}
    </li>
  );
}

export function ModelProviders(): JSX.Element {
  const role = useAuthStore((s) => s.user?.role);
  const { data, isLoading, isError, error } = useModelProviders();
  if (!canManageModelProviders(role)) {
    return <p className="text-sm text-ink-2">Only owners and admins can set the platform&rsquo;s model keys.</p>;
  }
  if (isLoading) return <p className="text-sm text-ink-3">Loading…</p>;
  if (isError || !data) {
    return <p className="text-sm text-red-600">{error instanceof Error ? error.message : 'Could not load the model providers'}</p>;
  }
  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-ink-2">
        A key is sent once to the model gateway, which checks it with the vendor and keeps it encrypted; it is never
        shown again. Every session can then pick that vendor&rsquo;s models.
      </p>
      {!data.editable ? (
        <p className="rounded-lg bg-line-soft px-3 py-2 text-sm text-ink-2" data-testid="keys-not-editable">
          This deployment takes model keys from the gateway&rsquo;s environment, so they cannot be changed here.
        </p>
      ) : null}
      <ul className="flex flex-col gap-2.5">
        {data.vendors.map((v) => (
          <VendorCard key={v.id} vendor={v} editable={data.editable} />
        ))}
      </ul>
    </div>
  );
}
