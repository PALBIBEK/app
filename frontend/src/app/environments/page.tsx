/**
 * Environments: named sandbox setups that several harnesses share. A harness picks one in the
 * builder's Sandbox tab; changing it here shows as an unpublished change on each harness that
 * uses it (publishing copies the settings into the release, so running versions never change).
 */

'use client';

import Link from 'next/link';
import { useState } from 'react';
import { CheckCircle, Cube, PencilSimple, Plus, Trash, WarningCircle } from '@phosphor-icons/react';
import { toast } from 'sonner';

import { AuthGuard } from '@/components/auth/AuthGuard';
import { SandboxFields } from '@/components/environments/SandboxFields';
import { Field, input } from '@/components/harness/builder/tabs';
import { Header } from '@/components/layout/Header';
import { Sidebar } from '@/components/layout/Sidebar';
import { useDeleteEnvironment, useEnvironments, useSaveEnvironment } from '@/lib/api/hooks/useEnvironments';
import type { Environment, EnvironmentWrite } from '@/lib/api/types/environment.types';
import { getApiErrorMessage } from '@/lib/api/errors';
import { describeSandbox, PRESETS } from '@/lib/environments/sandbox';

function EnvironmentForm({ initial, id, onDone }: { initial: EnvironmentWrite; id?: string; onDone: () => void }): JSX.Element {
  const [draft, setDraft] = useState<EnvironmentWrite>(initial);
  const save = useSaveEnvironment();

  async function submit(e: React.FormEvent): Promise<void> {
    e.preventDefault();
    try {
      await save.mutateAsync({ id, body: draft });
      toast.success(id ? 'Environment saved: harnesses that use it show an unpublished change' : 'Environment created');
      onDone();
    } catch (err) {
      toast.error(getApiErrorMessage(err, 'Could not save the environment'));
    }
  }

  return (
    <form onSubmit={(e) => void submit(e)} className="space-y-5 rounded-2xl border border-primary-200 bg-white p-5 shadow-sm" data-testid="environment-form">
      {!id ? (
        <div className="flex flex-wrap gap-2">
          <span className="text-xs font-medium text-slate-500">Start from:</span>
          {PRESETS.map((p) => (
            <button key={p.name} type="button" onClick={() => setDraft({ ...p })} className="rounded-full border border-slate-200 px-3 py-1 text-xs text-slate-700 hover:border-primary-300 hover:bg-primary-50">
              {p.name}
            </button>
          ))}
        </div>
      ) : null}
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Name" htmlFor="env-name">
          <input id="env-name" className={input} value={draft.name} maxLength={80} required onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
        </Field>
        <Field label="Description" htmlFor="env-description">
          <input id="env-description" className={input} value={draft.description} maxLength={500} onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
        </Field>
      </div>
      <SandboxFields value={draft.sandbox} onChange={(sandbox) => setDraft({ ...draft, sandbox })} idPrefix="env" />
      <div className="flex justify-end gap-2">
        <button type="button" onClick={onDone} className="rounded-xl px-4 py-2 text-sm text-slate-600 hover:bg-slate-100">Cancel</button>
        <button type="submit" disabled={save.isPending || !draft.name.trim()} className="rounded-xl bg-primary-600 px-4 py-2 text-sm font-semibold text-white hover:bg-primary-700 disabled:opacity-50" data-testid="environment-save">
          {save.isPending ? 'Saving…' : id ? 'Save' : 'Create environment'}
        </button>
      </div>
    </form>
  );
}

function EnvironmentCard({ env }: { env: Environment }): JSX.Element {
  const [editing, setEditing] = useState(false);
  const remove = useDeleteEnvironment();

  if (editing) {
    return <EnvironmentForm id={env.id} initial={{ name: env.name, description: env.description, sandbox: env.sandbox }} onDone={() => setEditing(false)} />;
  }

  async function del(): Promise<void> {
    if (!window.confirm(`Delete the environment “${env.name}”?`)) return;
    try {
      await remove.mutateAsync(env.id);
      toast.success('Environment deleted');
    } catch (err) {
      toast.error(getApiErrorMessage(err, 'Could not delete the environment'));
    }
  }

  return (
    <section id={env.id} data-testid="environment-card" className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex flex-wrap items-start gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary-50 text-primary-600"><Cube size={20} weight="bold" /></div>
        <div className="min-w-0 flex-1">
          <h2 className="text-base font-semibold text-slate-900">{env.name}</h2>
          {env.description ? <p className="text-sm text-slate-500">{env.description}</p> : null}
          <p className="mt-1 text-sm text-slate-700" data-testid="environment-settings">{describeSandbox(env.sandbox)}</p>
        </div>
        <button type="button" onClick={() => setEditing(true)} className="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50" aria-label={`Edit ${env.name}`}>
          <PencilSimple size={14} /> Edit
        </button>
        <button type="button" onClick={() => void del()} className="inline-flex items-center gap-1 rounded-lg px-2 py-1.5 text-sm text-red-600 hover:bg-red-50" aria-label={`Delete ${env.name}`}>
          <Trash size={14} />
        </button>
      </div>
      <div className="mt-4 border-t border-slate-100 pt-3 text-sm">
        {env.used_by.length === 0 ? (
          <p className="text-slate-500">No harness uses it yet: pick it in a harness’s Sandbox tab.</p>
        ) : (
          <ul className="flex flex-wrap gap-2" data-testid="environment-users">
            {env.used_by.map((u) => (
              <li key={u.id}>
                <Link href={`/harness/${u.id}`} className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 px-3 py-1 text-xs text-slate-700 hover:bg-slate-50">
                  {u.published ? <CheckCircle size={12} weight="fill" className="text-secondary-600" /> : <WarningCircle size={12} weight="fill" className="text-amber-500" />}
                  {u.name}
                  <span className="text-slate-400">{u.published ? 'published' : 'republish to apply'}</span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

function EnvironmentsContent(): JSX.Element {
  const { data, isLoading, error } = useEnvironments();
  const [creating, setCreating] = useState(false);
  const environments = data ?? [];

  return (
    <div className="mx-auto max-w-5xl px-8 py-8">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-primary-50 text-primary-600"><Cube size={22} weight="bold" /></div>
        <div className="flex-1">
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Environments</h1>
          <p className="text-sm text-slate-500">
            Sandbox setups your harnesses share: where commands run, what they may change, when the sandbox starts and when it stops.
          </p>
        </div>
        {!creating ? (
          <button type="button" onClick={() => setCreating(true)} className="inline-flex items-center gap-1.5 rounded-xl bg-slate-900 px-4 py-2 text-sm font-semibold text-white hover:bg-slate-800" data-testid="environment-new">
            <Plus size={16} weight="bold" /> New environment
          </button>
        ) : null}
      </div>

      <div className="mt-6 space-y-4">
        {creating ? <EnvironmentForm initial={{ ...PRESETS[0], name: '' }} onDone={() => setCreating(false)} /> : null}
        {isLoading && <div className="h-32 animate-pulse rounded-2xl bg-slate-100" />}
        {error && <p className="text-sm text-red-600">Could not load your environments.</p>}
        {!isLoading && !error && environments.length === 0 && !creating ? (
          <div className="rounded-2xl border border-dashed border-slate-200 bg-white/50 p-8 text-center text-sm text-slate-500">
            No environments yet. Make one to give several harnesses the same sandbox, changed in one place.
          </div>
        ) : null}
        {environments.map((env) => (
          <EnvironmentCard key={env.id} env={env} />
        ))}
      </div>
    </div>
  );
}

export default function EnvironmentsPage(): JSX.Element {
  return (
    <AuthGuard>
      <div className="grid min-h-[100dvh] grid-cols-[auto_1fr] grid-rows-[auto_1fr] bg-gradient-subtle">
        <div className="col-span-2">
          <Header />
        </div>
        <Sidebar />
        <main className="overflow-auto">
          <EnvironmentsContent />
        </main>
      </div>
    </AuthGuard>
  );
}
