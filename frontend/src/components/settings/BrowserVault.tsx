'use client';

/**
 * Saved logins, cards and addresses, on Policies & secrets: what the agent's browser fills
 * into a page for it, without the agent ever seeing a value (Hermes's vault kinds). A login
 * is kept for its site, with its authenticator key if the site asks for a second factor
 * (the agent's browser then types the code itself); a card or an address for the sites it
 * may be filled on, and a card only once the person allows each fill. Values are written,
 * never read back: the list shows names.
 */

import { useState } from 'react';
import { CreditCard, House, Key, Trash } from '@phosphor-icons/react';
import { toast } from 'sonner';

import { Button, Card, CardHeader, EmptyState, ErrorNote, Mono, Segmented, SkeletonRows } from '@/components/ui';
import { getApiErrorMessage } from '@/lib/api/errors';
import { useDeleteVaultItem, useSaveVaultItem, useVaultItems } from '@/lib/api/hooks/useOperate';
import type { VaultItem } from '@/lib/api/types/operate.types';

export const VAULT_PREFIXES = ['login:', 'card:', 'address:'] as const;

type Kind = 'login' | 'card' | 'address';

const KINDS: readonly { value: Kind; label: string }[] = [
  { value: 'login', label: 'Login' },
  { value: 'card', label: 'Card' },
  { value: 'address', label: 'Address' },
];

const ICONS: Record<Kind, JSX.Element> = {
  login: <Key size={15} className="text-ink-3" aria-hidden="true" />,
  card: <CreditCard size={15} className="text-ink-3" aria-hidden="true" />,
  address: <House size={15} className="text-ink-3" aria-hidden="true" />,
};

/** A label as the vault names an item by it: letters, digits, '.', '_', '-'. */
export function vaultLabel(text: string): string {
  return text.trim().replace(/\s+/g, '-').replace(/[^A-Za-z0-9._-]/g, '').replace(/^[^A-Za-z0-9]+/, '').slice(0, 60);
}

/** The sites a card or address may be filled on, one per line or comma. */
export function sitesOf(text: string): string[] {
  return text
    .split(/[\s,]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

function Field({
  id,
  label,
  value,
  onChange,
  type = 'text',
  placeholder,
  optional,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: string;
  placeholder?: string;
  optional?: boolean;
}): JSX.Element {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-[12.5px] text-ink-2">
        {label}
        {optional ? <span className="text-ink-3"> (optional)</span> : null}
      </label>
      <input
        id={id}
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        autoComplete={type === 'password' ? 'new-password' : 'off'}
        className="h-9 rounded-lg border border-line-strong bg-surface px-3 font-mono text-[13px]"
      />
    </div>
  );
}

const EMPTY = {
  origin: '',
  username: '',
  password: '',
  otp: '',
  label: '',
  sites: '',
  card_number: '',
  cardholder_name: '',
  exp_month: '',
  exp_year: '',
  cvc: '',
  address_line1: '',
  address_line2: '',
  city: '',
  state: '',
  postal_code: '',
  country: '',
};

type Form = typeof EMPTY;

function ready(kind: Kind, f: Form): boolean {
  if (kind === 'login') return !!(f.origin.trim() && f.username && f.password);
  const base = !!(vaultLabel(f.label) && sitesOf(f.sites).length);
  if (kind === 'card') return base && f.card_number.replace(/\s/g, '').length >= 12 && !!(f.exp_month && f.exp_year && f.cvc);
  return base && !!(f.address_line1 && f.city && f.postal_code && f.country);
}

function ItemRow({ item, onDelete, deleting }: { item: VaultItem; onDelete: (item: VaultItem) => void; deleting: boolean }): JSX.Element {
  return (
    <li className="flex items-center gap-3 px-4 py-2.5" data-testid={`vault-${item.handle}`}>
      {ICONS[item.kind]}
      <span className="w-16 text-[12.5px] capitalize text-ink-3">{item.kind}</span>
      <Mono className="min-w-0 flex-1 truncate text-[13px] text-ink">{item.name}</Mono>
      <span className="font-mono text-xs text-ink-3">••••••••</span>
      <Button size="sm" variant="danger" onClick={() => onDelete(item)} aria-label={`Delete ${item.handle}`} disabled={deleting}>
        <Trash size={14} />
      </Button>
    </li>
  );
}

export function BrowserVault(): JSX.Element {
  const { data, isLoading, error, refetch } = useVaultItems();
  const save = useSaveVaultItem();
  const del = useDeleteVaultItem();
  const [kind, setKind] = useState<Kind>('login');
  const [form, setForm] = useState<Form>(EMPTY);
  const set = (key: keyof Form) => (value: string) => setForm((f) => ({ ...f, [key]: value }));
  const items = data ?? [];

  async function submit(e: React.FormEvent): Promise<void> {
    e.preventDefault();
    if (!ready(kind, form)) return;
    const sites = sitesOf(form.sites);
    const label = vaultLabel(form.label);
    try {
      if (kind === 'login') {
        await save.mutateAsync({ kind, value: { origin: form.origin.trim(), username: form.username, password: form.password, ...(form.otp.trim() ? { otp: form.otp.trim() } : {}) } });
      } else if (kind === 'card') {
        await save.mutateAsync({
          kind,
          value: {
            label,
            origins: sites,
            card_number: form.card_number.replace(/\s/g, ''),
            exp_month: form.exp_month.trim(),
            exp_year: form.exp_year.trim(),
            cvc: form.cvc.trim(),
            ...(form.cardholder_name.trim() ? { cardholder_name: form.cardholder_name.trim() } : {}),
          },
        });
      } else {
        await save.mutateAsync({
          kind,
          value: {
            label,
            origins: sites,
            address_line1: form.address_line1,
            city: form.city,
            postal_code: form.postal_code,
            country: form.country,
            ...(form.address_line2 ? { address_line2: form.address_line2 } : {}),
            ...(form.state ? { state: form.state } : {}),
          },
        });
      }
      toast.success(kind === 'login' ? `Saved the login for ${form.origin.trim()}` : `Saved ${label}`);
      setForm(EMPTY);
    } catch (err) {
      toast.error(getApiErrorMessage(err, `Could not save the ${kind}`));
    }
  }

  async function remove(item: VaultItem): Promise<void> {
    if (!window.confirm(`Delete ${item.kind} ${item.name}? The agent's browser can no longer fill it.`)) return;
    try {
      await del.mutateAsync(item.handle);
    } catch (err) {
      toast.error(getApiErrorMessage(err, `Could not delete ${item.name}`));
    }
  }

  return (
    <Card aria-labelledby="browser-vault-title" id="browser-vault" className="scroll-mt-6">
      <CardHeader
        title={<span id="browser-vault-title">Saved logins, cards and addresses</span>}
        description="The agent's browser fills these into a page for it, only on their sites, and never shows them to it. A card is filled only once you allow each payment. Values are encrypted and never shown again."
      />
      <div className="grid gap-0 lg:grid-cols-[1fr_360px]">
        <div className="min-w-0 border-line-soft lg:border-r">
          {isLoading ? (
            <SkeletonRows rows={3} />
          ) : error ? (
            <div className="p-4">
              <ErrorNote message={getApiErrorMessage(error, 'Could not load the saved items')} onRetry={() => void refetch()} />
            </div>
          ) : items.length === 0 ? (
            <EmptyState icon={<Key size={26} />} title="Nothing saved yet" description="Save a login (and its authenticator key), a card or an address for the agent's browser to fill." />
          ) : (
            <ul className="divide-y divide-line-soft" data-testid="vault-items">
              {items.map((item) => (
                <ItemRow key={item.handle} item={item} onDelete={(i) => void remove(i)} deleting={del.isPending} />
              ))}
            </ul>
          )}
        </div>
        <form onSubmit={(e) => void submit(e)} className="flex flex-col gap-2 border-t border-line-soft p-4 lg:border-t-0" aria-label="Save an item">
          <Segmented label="What to save" options={KINDS} value={kind} onChange={setKind} />
          {kind === 'login' ? (
            <>
              <Field id="vault-origin" label="Site" value={form.origin} onChange={set('origin')} placeholder="https://shop.example.com" />
              <Field id="vault-username" label="Username or email" value={form.username} onChange={set('username')} />
              <Field id="vault-password" label="Password" type="password" value={form.password} onChange={set('password')} />
              <Field id="vault-otp" label="Authenticator key" type="password" value={form.otp} onChange={set('otp')} placeholder="base32 key or otpauth:// link" optional />
            </>
          ) : (
            <>
              <Field id="vault-label" label="Name" value={form.label} onChange={set('label')} placeholder={kind === 'card' ? 'Visa-4242' : 'Home'} />
              <Field id="vault-sites" label="Sites it may be filled on" value={form.sites} onChange={set('sites')} placeholder="https://shop.example.com" />
              {kind === 'card' ? (
                <>
                  <Field id="vault-number" label="Card number" type="password" value={form.card_number} onChange={set('card_number')} />
                  <Field id="vault-holder" label="Name on card" value={form.cardholder_name} onChange={set('cardholder_name')} optional />
                  <div className="grid grid-cols-3 gap-2">
                    <Field id="vault-month" label="Month" value={form.exp_month} onChange={set('exp_month')} placeholder="04" />
                    <Field id="vault-year" label="Year" value={form.exp_year} onChange={set('exp_year')} placeholder="2031" />
                    <Field id="vault-cvc" label="CVC" type="password" value={form.cvc} onChange={set('cvc')} />
                  </div>
                </>
              ) : (
                <>
                  <Field id="vault-line1" label="Address" value={form.address_line1} onChange={set('address_line1')} />
                  <Field id="vault-line2" label="Address line 2" value={form.address_line2} onChange={set('address_line2')} optional />
                  <div className="grid grid-cols-2 gap-2">
                    <Field id="vault-city" label="City" value={form.city} onChange={set('city')} />
                    <Field id="vault-state" label="State or region" value={form.state} onChange={set('state')} optional />
                    <Field id="vault-postal" label="Postal code" value={form.postal_code} onChange={set('postal_code')} />
                    <Field id="vault-country" label="Country" value={form.country} onChange={set('country')} />
                  </div>
                </>
              )}
            </>
          )}
          <Button type="submit" variant="primary" disabled={!ready(kind, form) || save.isPending}>
            {save.isPending ? 'Saving…' : `Save ${kind}`}
          </Button>
        </form>
      </div>
    </Card>
  );
}
