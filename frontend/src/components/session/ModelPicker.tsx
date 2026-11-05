'use client';

/**
 * Which model the session runs next: the models that can run now, grouped by vendor, as QM's
 * composer offers them (``plugins/web-ui/src/model-picker.ts``). A vendor without a key (or a
 * local server that is not set up) shows as not ready, with where to fix it. The session's
 * current model stays listed even when it is not offered now, so the picker never pretends it
 * changed.
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

function modelLabel(m: ModelChoice): string {
  return m.label || m.name || m.id;
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
}

export function ModelPicker({ value, current, choices, onChange, disabled, id }: ModelPickerProps): JSX.Element | null {
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
              <option key={m.id} value={m.id}>
                {modelLabel(m)}
                {m.vendor === 'local' ? ' (local)' : ''}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
      {notReady.length > 0 ? (
        <Link
          href={SETTINGS_HREF}
          className="truncate text-ink-3 hover:text-ink-2 hover:underline"
          title={`Not ready: ${notReady.map((p) => p.label || p.id).join(', ')}`}
        >
          More models in Settings
        </Link>
      ) : null}
    </div>
  );
}
