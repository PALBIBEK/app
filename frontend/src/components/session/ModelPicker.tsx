'use client';

/**
 * Which model the session runs next: the models that can run now, grouped by vendor, as QM's
 * composer offers them (``plugins/web-ui/src/model-picker.ts``). A vendor that cannot run yet
 * is listed too, its entry disabled, saying why (no API key; a local server not set up or not
 * answering) and who can fix it: an owner or admin gets a link to the model providers, anyone
 * else is told to ask one. The session's current model stays listed even when it is not
 * offered now, so the picker never pretends it changed.
 */

import Link from 'next/link';

import type { ModelChoice, ModelChoices } from '@/lib/api/types/model.types';

/** Where a vendor's key is set (Policies & secrets, its model providers). */
export const SETTINGS_HREF = '/policies#model-providers';

interface Group {
  vendor: string;
  label: string;
  models: ModelChoice[];
}

/** The offered models by vendor, in the order the vendors are listed; models with an unknown
 * vendor last. */
export function groupByVendor(choices: ModelChoices): Group[] {
  const labels = new Map(choices.providers.map((p) => [p.id, p.label || p.id]));
  const groups = new Map<string, Group>();
  for (const p of choices.providers) groups.set(p.id, { vendor: p.id, label: labels.get(p.id)!, models: [] });
  for (const m of choices.models) {
    const vendor = m.vendor || 'other';
    if (!groups.has(vendor)) groups.set(vendor, { vendor, label: labels.get(vendor) ?? vendor, models: [] });
    groups.get(vendor)!.models.push(m);
  }
  return [...groups.values()].filter((g) => g.models.length > 0);
}

/** Why a vendor offers no model now, in a few words. */
export function notReadyReason(vendor: string): string {
  return vendor === 'local' ? 'no local server set up or answering' : 'no API key';
}

/** Who can make a provider ready, and where: a link for those who can, words for the rest. */
export function SetupHint({ canManage }: { canManage: boolean }): JSX.Element {
  return canManage ? (
    <Link href={SETTINGS_HREF} className="font-medium text-accent-700 hover:underline">
      Set up model providers
    </Link>
  ) : (
    <span className="text-ink-2">Ask an owner or admin to set it up, or pick another model.</span>
  );
}

function modelLabel(m: ModelChoice): string {
  return m.label || m.name || m.id;
}

const dollars = (n: number): string => `$${Number(n.toFixed(3))}`;
const PRICE_TITLE = 'Price per million tokens: input / output';

/** A model's list price per million tokens, input / output, as the picker shows it. */
export function priceLabel(m: ModelChoice): string | null {
  const input = m.cost?.input;
  const output = m.cost?.output;
  if (typeof input !== 'number' || typeof output !== 'number') return null;
  return input === 0 && output === 0 ? 'free' : `${dollars(input)} / ${dollars(output)}`;
}

/** What a model is, in one line: its name, then its price (per million input / output tokens). */
export function optionText(m: ModelChoice): string {
  const price = priceLabel(m);
  const where = m.vendor === 'local' ? 'local' : null;
  const extra = [where, price].filter(Boolean).join(', ');
  return extra ? `${modelLabel(m)} (${extra})` : modelLabel(m);
}

export interface ModelPickerProps {
  /** The model selected for the next message. */
  value: string;
  /** The model the session runs now (its owner's choice, else the agent's). */
  current: string | null | undefined;
  choices: ModelChoices | undefined;
  onChange: (model: string) => void;
  disabled?: boolean;
  id: string;
  /** Whether the person can set providers up (owners and admins). */
  canManage?: boolean;
}

export function ModelPicker({ value, current, choices, onChange, disabled, id, canManage = false }: ModelPickerProps): JSX.Element | null {
  if (!choices || (choices.models.length === 0 && !current)) return null;
  const groups = groupByVendor(choices);
  const offered = new Set(choices.models.map((m) => m.id));
  const notReady = choices.providers.filter((p) => !p.ready);
  return (
    <div className="flex min-w-0 items-center gap-1.5 text-xs text-ink-2">
      <label htmlFor={id}>Model</label>
      <select
        id={id}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        className="h-7 min-w-0 max-w-[16rem] truncate rounded-md border border-line-strong bg-surface px-1.5 text-xs text-ink disabled:cursor-not-allowed"
      >
        {current && !offered.has(current) ? (
          <option value={current}>{current} (this session&rsquo;s)</option>
        ) : null}
        {groups.map((g) => (
          <optgroup key={g.vendor} label={g.label}>
            {g.models.map((m) => (
              <option key={m.id} value={m.id} title={PRICE_TITLE}>
                {optionText(m)}
              </option>
            ))}
          </optgroup>
        ))}
        {notReady.map((p) => (
          <optgroup key={p.id} label={p.label || p.id}>
            <option value={`not-ready:${p.id}`} disabled>
              Not available: {notReadyReason(p.id)}
            </option>
          </optgroup>
        ))}
      </select>
      {notReady.length > 0 ? (
        canManage ? (
          <Link
            href={SETTINGS_HREF}
            className="truncate text-ink-3 hover:text-ink-2 hover:underline"
            title={`Not available yet: ${notReady.map((p) => p.label || p.id).join(', ')}`}
          >
            Add model providers
          </Link>
        ) : (
          <span className="truncate text-ink-3" title="An owner or admin adds model providers in Policies & secrets">
            More models: ask an admin
          </span>
        )
      ) : null}
    </div>
  );
}
