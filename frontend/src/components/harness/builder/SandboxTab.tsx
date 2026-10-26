'use client';

/**
 * The builder's Sandbox tab: use a shared environment (its settings, read only here) or set
 * this harness's own sandbox.
 */

import Link from 'next/link';
import { Cube, PencilSimple } from '@phosphor-icons/react';

import { SandboxFields } from '@/components/environments/SandboxFields';
import { useEnvironments } from '@/lib/api/hooks/useEnvironments';
import type { Manifest } from '@/lib/api/types/manifest.types';
import { describeSandbox } from '@/lib/environments/sandbox';

import { Field, input } from './tabs';

export function SandboxTab({ m, onChange }: { m: Manifest; onChange: (next: Manifest) => void }): JSX.Element {
  const { data: environments = [], isLoading } = useEnvironments();
  const chosen = environments.find((e) => e.id === m.environment_id);

  function pick(id: string): void {
    if (!id) {
      onChange({ ...m, environment_id: null });  // keep the settings it had, now its own
      return;
    }
    const env = environments.find((e) => e.id === id);
    onChange({ ...m, environment_id: id, sandbox: env ? env.sandbox : m.sandbox });
  }

  return (
    <div className="space-y-6">
      <Field
        label="Environment"
        htmlFor="environment"
        hint={
          <>
            Share one sandbox setup between harnesses; change it once on{' '}
            <Link href="/environments" className="font-medium text-accent-600 hover:text-accent-700">Environments</Link>.
          </>
        }
      >
        <select id="environment" className={input} value={m.environment_id ?? ''} onChange={(e) => pick(e.target.value)} disabled={isLoading} data-testid="environment-picker">
          <option value="">This harness’s own settings</option>
          {environments.map((e) => (
            <option key={e.id} value={e.id}>{e.name}</option>
          ))}
          {m.environment_id && !chosen && !isLoading ? <option value={m.environment_id}>(deleted environment)</option> : null}
        </select>
      </Field>
      {chosen ? (
        <div className="flex items-start gap-3 rounded-xl border border-accent-200 bg-primary-50/50 px-4 py-3 text-sm" data-testid="environment-summary">
          <Cube size={18} className="mt-0.5 shrink-0 text-accent-600" />
          <div className="min-w-0 flex-1">
            <p className="font-semibold text-ink">{chosen.name}</p>
            <p className="text-ink-2">{describeSandbox(chosen.sandbox)}</p>
            <p className="mt-1 text-xs text-ink-2">Set by the environment; publishing copies these settings into the release.</p>
          </div>
          <Link href={`/environments#${chosen.id}`} className="inline-flex items-center gap-1 rounded-lg border border-line bg-surface px-2.5 py-1 text-xs text-ink-2 hover:bg-canvas">
            <PencilSimple size={12} /> Edit
          </Link>
        </div>
      ) : null}
      <SandboxFields
        value={m.sandbox}
        onChange={(sandbox) => onChange({ ...m, sandbox })}
        disabled={Boolean(m.environment_id)}
        tools={m.tools}
      />
    </div>
  );
}
