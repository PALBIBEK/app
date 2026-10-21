/**
 * Long-term memory: what each harness remembers about you, by notebook and version.
 *
 * Facts can be added, corrected and forgotten; every change is kept in the notebook's
 * history, from which an earlier state can be restored.
 */

'use client';

import { Suspense, useEffect, useMemo, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { isAxiosError } from 'axios';
import { Archive, Brain, ClockCounterClockwise, MagnifyingGlass, PencilSimple, Plus, Trash, WarningCircle } from '@phosphor-icons/react';

import { AuthGuard } from '@/components/auth/AuthGuard';
import { Header } from '@/components/layout/Header';
import { Sidebar } from '@/components/layout/Sidebar';
import { useHarnesses } from '@/lib/api/hooks/useHarnesses';
import {
  useAddFacts,
  useArchiveNotebook,
  useClearNotebook,
  useCreateNotebook,
  useEditFacts,
  useFacts,
  useForgetFacts,
  useNotebooks,
  useRestoreNotebook,
  useRevisions,
  useSearchMemory,
} from '@/lib/api/hooks/useMemory';
import type { Notebook } from '@/lib/api/types/memory.types';
import { cn } from '@/lib/utils';

const input = 'rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm focus:border-primary-500 focus:outline-none';

function unavailable(error: unknown): string | null {
  if (!error) return null;
  if (isAxiosError(error) && error.response?.status === 503) {
    return 'Long-term memory is unavailable right now. Conversations still work; they just recall nothing until it is back.';
  }
  if (isAxiosError(error) && error.response?.status === 422) return 'Publish this harness first: it has no memory until a version runs.';
  return 'Could not load memory.';
}

function NotebookView({ notebook }: { notebook: Notebook }): JSX.Element {
  const { data: facts = [], isLoading } = useFacts(notebook.id);
  const [showHistory, setShowHistory] = useState(false);
  const { data: revisions = [] } = useRevisions(showHistory ? notebook.id : undefined);
  const add = useAddFacts();
  const edit = useEditFacts();
  const forget = useForgetFacts();
  const clear = useClearNotebook();
  const restore = useRestoreNotebook();
  const archive = useArchiveNotebook();
  const [draft, setDraft] = useState('');
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null);

  const states = useMemo(() => [...new Set(revisions.map((r) => r.revision))].sort((a, b) => b - a), [revisions]);

  return (
    <section data-testid="notebook" className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-base font-semibold text-slate-900">{notebook.label || notebook.name}</h2>
        <span className="text-xs text-slate-500">
          {notebook.facts} facts · revision {notebook.revision}
          {notebook.version != null ? ` · version ${notebook.version}` : ''}
        </span>
        <span className="ml-auto flex items-center gap-1">
          <button type="button" onClick={() => setShowHistory((s) => !s)} className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-slate-600 hover:bg-slate-100">
            <ClockCounterClockwise size={14} /> History
          </button>
          <button
            type="button"
            onClick={() => window.confirm('Forget every fact in this notebook? You can restore it from its history.') && clear.mutate(notebook.id)}
            className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-slate-600 hover:bg-slate-100"
          >
            <Trash size={14} /> Clear
          </button>
          <button type="button" onClick={() => archive.mutate({ notebookId: notebook.id, archived: !notebook.archived })} className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-slate-600 hover:bg-slate-100">
            <Archive size={14} /> {notebook.archived ? 'Unarchive' : 'Archive'}
          </button>
        </span>
      </div>

      {isLoading ? (
        <p className="mt-4 text-sm text-slate-500">Loading…</p>
      ) : facts.length === 0 ? (
        <p className="mt-4 text-sm text-slate-500">Nothing remembered yet.</p>
      ) : (
        <ul data-testid="facts" className="mt-4 divide-y divide-slate-100">
          {facts.map((f) => (
            <li key={f.id} className="flex items-start gap-3 py-2">
              {editing?.id === f.id ? (
                <form
                  className="flex flex-1 gap-2"
                  onSubmit={(e) => {
                    e.preventDefault();
                    edit.mutate({ notebookId: notebook.id, edits: [{ id: f.id, text: editing.text }] }, { onSuccess: () => setEditing(null) });
                  }}
                >
                  <input aria-label="Edit fact" className={cn(input, 'flex-1')} value={editing.text} onChange={(e) => setEditing({ id: f.id, text: e.target.value })} autoFocus />
                  <button type="submit" className="rounded-lg bg-primary-600 px-3 text-xs font-medium text-white">Save</button>
                  <button type="button" onClick={() => setEditing(null)} className="text-xs text-slate-500">Cancel</button>
                </form>
              ) : (
                <>
                  <span className="flex-1 text-sm text-slate-800">{f.text}</span>
                  {['sensitive', 'restricted'].includes(f.sensitivity) ? <span className="rounded-full bg-slate-100 px-2 text-[10px] text-slate-600">{f.sensitivity}</span> : null}
                  <button type="button" aria-label={`Edit ${f.text}`} onClick={() => setEditing({ id: f.id, text: f.text })} className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"><PencilSimple size={14} /></button>
                  <button type="button" aria-label={`Forget ${f.text}`} onClick={() => forget.mutate({ notebookId: notebook.id, ids: [f.id] })} className="rounded p-1 text-slate-400 hover:bg-red-50 hover:text-red-600"><Trash size={14} /></button>
                </>
              )}
            </li>
          ))}
        </ul>
      )}

      <form
        className="mt-4 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          const text = draft.trim();
          if (text) add.mutate({ notebookId: notebook.id, facts: [text] }, { onSuccess: () => setDraft('') });
        }}
      >
        <input aria-label="New fact" placeholder="Something to remember, e.g. I prefer tabs" className={cn(input, 'flex-1')} value={draft} onChange={(e) => setDraft(e.target.value)} />
        <button type="submit" disabled={!draft.trim()} className="inline-flex items-center gap-1 rounded-lg bg-primary-600 px-3 text-sm font-medium text-white disabled:opacity-50">
          <Plus size={14} /> Add
        </button>
      </form>

      {showHistory ? (
        <div data-testid="history" className="mt-4 rounded-xl bg-slate-50 p-3">
          <p className="text-xs font-medium text-slate-600">History, newest first: restore the notebook to how it was after a change.</p>
          {states.map((rev) => (
            <div key={rev} className="mt-2 border-t border-slate-200 pt-2 first:border-0">
              <div className="flex items-center gap-2 text-xs">
                <span className="font-medium text-slate-700">Revision {rev}</span>
                <span className="text-slate-400">{new Date(revisions.find((r) => r.revision === rev)?.at ?? 0).toLocaleString()}</span>
                {rev !== notebook.revision ? (
                  <button type="button" onClick={() => restore.mutate({ notebookId: notebook.id, revision: rev })} className="ml-auto rounded-lg border border-slate-200 bg-white px-2 py-0.5 text-slate-700 hover:bg-slate-100">
                    Restore
                  </button>
                ) : (
                  <span className="ml-auto text-slate-400">current</span>
                )}
              </div>
              <ul className="mt-1 space-y-0.5">
                {revisions.filter((r) => r.revision === rev).map((r, i) => (
                  <li key={`${r.fact_id}-${i}`} className="text-xs text-slate-600">
                    <span className={cn('mr-1 font-mono', r.op === 'delete' ? 'text-red-600' : r.op === 'insert' ? 'text-secondary-700' : 'text-amber-700')}>{r.op}</span>
                    {r.text}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function MemoryContent(): JSX.Element {
  const router = useRouter();
  const params = useSearchParams();
  const { data: harnessList } = useHarnesses();
  const harnesses = (harnessList?.data ?? []).filter((h) => h.bind?.bind_status === 'bound');
  const harnessId = params.get('harness') ?? harnesses[0]?.id;
  const [showArchived, setShowArchived] = useState(false);
  const { data: notebooks = [], error, isLoading } = useNotebooks(harnessId, showArchived, true);
  const selectedId = params.get('notebook') ?? notebooks[0]?.id;
  const selected = notebooks.find((n) => n.id === selectedId);
  const create = useCreateNotebook();
  const search = useSearchMemory();
  const [query, setQuery] = useState('');
  const [newName, setNewName] = useState('');

  useEffect(() => search.reset(), [harnessId]); // eslint-disable-line react-hooks/exhaustive-deps

  const go = (patch: Record<string, string | undefined>) => {
    const next = new URLSearchParams(params.toString());
    for (const [k, v] of Object.entries(patch)) (v ? next.set(k, v) : next.delete(k));
    router.replace(`/memory?${next.toString()}`);
  };

  const byVersion = useMemo(() => {
    const groups = new Map<string, Notebook[]>();
    for (const nb of notebooks) {
      const key = nb.version != null ? `Version ${nb.version}` : 'Current version';
      groups.set(key, [...(groups.get(key) ?? []), nb]);
    }
    return [...groups.entries()];
  }, [notebooks]);

  const problem = unavailable(error);
  return (
    <div className="mx-auto max-w-6xl px-8 py-8">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-accent-50 text-accent-600"><Brain size={22} weight="fill" /></div>
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Memory</h1>
          <p className="text-sm text-slate-500">What each harness remembers about you. Only you see your notebooks.</p>
        </div>
        <select aria-label="Harness" className={cn(input, 'ml-auto')} value={harnessId ?? ''} onChange={(e) => go({ harness: e.target.value, notebook: undefined })}>
          {harnesses.length === 0 ? <option value="">No published harness</option> : null}
          {harnesses.map((h) => (
            <option key={h.id} value={h.id}>{h.name}</option>
          ))}
        </select>
      </div>

      {problem ? (
        <div role="alert" data-testid="memory-unavailable" className="mt-6 flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <WarningCircle size={18} className="mt-0.5 shrink-0" /> {problem}
        </div>
      ) : null}

      {harnessId && !problem ? (
        <div className="mt-6 grid gap-6 lg:grid-cols-[16rem_1fr]">
          <aside className="space-y-4">
            <form
              className="flex gap-1"
              onSubmit={(e) => {
                e.preventDefault();
                if (query.trim()) search.mutate({ harness_id: harnessId, query: query.trim() });
              }}
            >
              <input aria-label="Search memory" placeholder="Search what it remembers" className={cn(input, 'min-w-0 flex-1')} value={query} onChange={(e) => setQuery(e.target.value)} />
              <button type="submit" aria-label="Search" className="rounded-lg border border-slate-200 bg-white px-2"><MagnifyingGlass size={14} /></button>
            </form>
            {search.data ? (
              <ul data-testid="search-results" className="space-y-1 text-xs">
                {search.data.length === 0 ? <li className="text-slate-500">No match in the live version&apos;s notebooks.</li> : null}
                {search.data.map((hit, i) => (
                  <li key={i}>
                    <button type="button" onClick={() => go({ notebook: hit.notebook_id })} className="text-left text-slate-700 hover:text-primary-700">{hit.fact.replace(/^#\d+ \(\d{4}-\d{2}-\d{2}\) /, '')}</button>
                  </li>
                ))}
              </ul>
            ) : null}

            {isLoading ? <p className="text-sm text-slate-500">Loading…</p> : null}
            {byVersion.map(([label, list]) => (
              <div key={label}>
                <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">{label}</p>
                <ul className="mt-1 space-y-1">
                  {list.map((nb) => (
                    <li key={nb.id}>
                      <button type="button" onClick={() => go({ notebook: nb.id })} className={cn('flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-sm', nb.id === selected?.id ? 'bg-primary-50 text-primary-800' : 'text-slate-700 hover:bg-slate-100')}>
                        <span className="truncate">{nb.label || nb.name}{nb.archived ? ' (archived)' : ''}</span>
                        <span className="text-xs text-slate-400">{nb.facts}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
            <form
              className="flex gap-1"
              onSubmit={(e) => {
                e.preventDefault();
                const name = newName.trim();
                if (name) create.mutate({ harness_id: harnessId, name }, { onSuccess: (nb) => { setNewName(''); go({ notebook: nb.id }); } });
              }}
            >
              <input aria-label="New notebook" placeholder="New notebook" className={cn(input, 'min-w-0 flex-1')} value={newName} onChange={(e) => setNewName(e.target.value)} />
              <button type="submit" disabled={!newName.trim()} className="rounded-lg bg-primary-600 px-2 text-white disabled:opacity-50"><Plus size={14} /></button>
            </form>
            <label className="flex items-center gap-2 text-xs text-slate-500">
              <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} /> Show archived
            </label>
          </aside>
          <div>{selected ? <NotebookView key={selected.id} notebook={selected} /> : <p className="text-sm text-slate-500">No notebooks yet: chatting with the harness creates one.</p>}</div>
        </div>
      ) : null}
    </div>
  );
}

export default function MemoryPage(): JSX.Element {
  return (
    <AuthGuard>
      <div className="grid min-h-[100dvh] grid-cols-[auto_1fr] grid-rows-[auto_1fr] bg-gradient-subtle">
        <div className="col-span-2">
          <Header />
        </div>
        <Sidebar />
        <main className="overflow-auto">
          <Suspense fallback={null}>
            <MemoryContent />
          </Suspense>
        </main>
      </div>
    </AuthGuard>
  );
}
