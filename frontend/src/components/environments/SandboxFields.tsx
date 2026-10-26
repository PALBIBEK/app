'use client';

/**
 * A sandbox's settings: where commands run, what they may change, approvals, and when the
 * sandbox starts and stops. Used by the builder (a harness's own sandbox) and by environments.
 */

import { Warning } from '@phosphor-icons/react';

import { Field, input } from '@/components/harness/builder/tabs';
import type { SandboxSpec } from '@/lib/api/types/manifest.types';
import { IDLE_STOPS, PROVIDERS, STARTS } from '@/lib/environments/sandbox';
import { cn } from '@/lib/utils';

export interface SandboxFieldsProps {
  value: SandboxSpec;
  onChange?: (next: SandboxSpec) => void;
  /** Read only: the settings come from an environment. */
  disabled?: boolean;
  /** The harness's tools, to warn when bash cannot run on a host process. */
  tools?: string[];
  /** Prefix for the fields' ids (two forms on one page); none: plain ids (file_effects, …). */
  idPrefix?: string;
}

export function SandboxFields({ value: s, onChange, disabled, tools = [], idPrefix }: SandboxFieldsProps): JSX.Element {
  const set = (patch: Partial<SandboxSpec>) => onChange?.({ ...s, ...patch });
  const id = (name: string) => (idPrefix ? `${idPrefix}-${name}` : name);
  const shellBlocked = s.provider === 'local_process' && s.file_effects !== 'danger_full_access' && tools.includes('bash');
  return (
    <fieldset disabled={disabled} className="space-y-6 disabled:opacity-80">
      <div className="space-y-2">
        <p className="text-sm font-medium text-ink-2">Where commands run</p>
        {PROVIDERS.map((p) => (
          <label
            key={p.id}
            className={cn(
              'flex items-start gap-3 rounded-xl border px-4 py-3',
              disabled ? 'cursor-default' : 'cursor-pointer',
              s.provider === p.id ? 'border-accent-500 bg-primary-50/50' : 'border-line bg-surface hover:border-line-strong'
            )}
          >
            <input type="radio" name={id('provider')} className="mt-1" checked={s.provider === p.id} onChange={() => set({ provider: p.id })} />
            <span>
              <span className="block text-sm font-semibold text-ink">{p.label}</span>
              <span className="block text-xs text-ink-2">{p.hint}</span>
            </span>
          </label>
        ))}
      </div>
      <Field label="What the agent may change" htmlFor={id('file_effects')}>
        <select id={id('file_effects')} className={input} value={s.file_effects} onChange={(e) => set({ file_effects: e.target.value as SandboxSpec['file_effects'] })}>
          <option value="read_only">Read only</option>
          <option value="workspace_write">Its workspace</option>
          <option value="danger_full_access">Everything the sandbox can reach</option>
        </select>
      </Field>
      {shellBlocked ? (
        <p className="flex items-start gap-2 rounded-xl bg-accent-50 px-4 py-3 text-sm text-ink">
          <Warning size={16} className="mt-0.5 shrink-0" />
          On a host process, <code>bash</code> only runs with “Everything the sandbox can reach”: without an isolating
          sandbox a command could change the machine. Use Docker or E2B to keep it contained.
        </p>
      ) : null}
      <Field label="Tool calls by default" htmlFor={id('approval_default')} hint="Tools marked “Ask first” always ask, whatever this says.">
        <select id={id('approval_default')} className={input} value={s.approval_default} onChange={(e) => set({ approval_default: e.target.value as SandboxSpec['approval_default'] })}>
          <option value="ask">Ask when the sandbox needs to</option>
          <option value="never">Never ask</option>
        </select>
      </Field>
      {s.provider !== 'none' ? (
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Start the sandbox" htmlFor={id('start')} hint="Starting with the run hides the start-up time behind the model's first step.">
            <select id={id('start')} className={input} value={s.start ?? ''} onChange={(e) => set({ start: (e.target.value || null) as SandboxSpec['start'] })}>
              {STARTS.map((o) => (
                <option key={o.value ?? ''} value={o.value ?? ''}>{o.label}</option>
              ))}
            </select>
          </Field>
          <Field label="Stop it when unused" htmlFor={id('idle_stop_s')} hint="Files are saved first and come back on the next tool call; running processes do not.">
            <select
              id={id('idle_stop_s')}
              className={input}
              value={s.idle_stop_s == null ? '' : String(s.idle_stop_s)}
              onChange={(e) => set({ idle_stop_s: e.target.value === '' ? null : Number(e.target.value) })}
            >
              {IDLE_STOPS.some((o) => o.value === (s.idle_stop_s ?? '')) ? null : (
                <option value={String(s.idle_stop_s)}>After {s.idle_stop_s} seconds</option>
              )}
              {IDLE_STOPS.map((o) => (
                <option key={String(o.value)} value={String(o.value)}>{o.label}</option>
              ))}
            </select>
          </Field>
        </div>
      ) : null}
    </fieldset>
  );
}
