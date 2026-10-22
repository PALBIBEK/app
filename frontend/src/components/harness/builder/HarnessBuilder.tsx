'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { isAxiosError } from 'axios';
import { DownloadSimple, Play, RocketLaunch, UploadSimple } from '@phosphor-icons/react';
import { toast } from 'sonner';

import {
  useCatalogModels,
  useCatalogTools,
  useManifest,
  usePublishHarness,
  useReleases,
  useSaveManifest,
} from '@/lib/api/hooks/useManifest';
import type { Harness } from '@/lib/api/types/harness.types';
import { SECTIONS, type Manifest, type Release, type Section } from '@/lib/api/types/manifest.types';
import { diffManifests, errorsBySection, exportHarness, parseImport, type SectionErrors } from '@/lib/harness/manifest';
import { cn } from '@/lib/utils';

import { SandboxTab } from './SandboxTab';
import { Errors, MemoryTab, ModelTab, PolicyTab, PromptTab, ToolsTab } from './tabs';

const TAB_LABEL: Record<Section, string> = {
  prompt: 'Prompt',
  tools: 'Tools',
  model: 'Model',
  sandbox: 'Sandbox',
  memory: 'Memory',
  policy: 'Policy',
};

function apiErrors(error: unknown): SectionErrors {
  if (isAxiosError(error) && error.response?.data) return errorsBySection(error.response.data);
  return { general: [error instanceof Error ? error.message : 'Something went wrong'] };
}

export function HarnessBuilder({ harness }: { harness: Harness }): JSX.Element {
  const { data: view, isLoading } = useManifest(harness.id);
  const { data: releases } = useReleases(harness.id);
  const { data: tools } = useCatalogTools();
  const { data: models } = useCatalogModels();
  const save = useSaveManifest(harness.id);
  const publish = usePublishHarness(harness.id);

  const [draft, setDraft] = useState<Manifest | null>(null);
  const [tab, setTab] = useState<Section>('prompt');
  const [errors, setErrors] = useState<SectionErrors>({});
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (view && draft === null) setDraft(view.manifest);
  }, [view, draft]);

  const dirty = useMemo(() => Boolean(view && draft && diffManifests(view.manifest, draft).length), [view, draft]);
  const latest: Release | undefined = releases?.[0];
  const sinceLatest = useMemo(() => (latest && draft ? diffManifests(latest.manifest, draft) : []), [latest, draft]);

  if (isLoading || !draft || !view) {
    return <p className="mt-8 text-sm text-slate-500">Loading the manifest…</p>;
  }

  async function saveDraft(): Promise<boolean> {
    try {
      await save.mutateAsync(draft!);
      setErrors({});
      return true;
    } catch (error) {
      const found = apiErrors(error);
      setErrors(found);
      const first = SECTIONS.find((s) => found[s]?.length);
      if (first) setTab(first);
      return false;
    }
  }

  async function handlePublish(): Promise<void> {
    if (dirty && !(await saveDraft())) return;
    try {
      const out = await publish.mutateAsync();
      setErrors({});
      toast.success(out.created ? `Published version ${out.release.version}` : `No changes since version ${out.release.version}`);
    } catch (error) {
      const found = apiErrors(error);
      setErrors(found);
      const first = SECTIONS.find((s) => found[s]?.length);
      if (first) setTab(first);
    }
  }

  function handleExport(): void {
    const blob = new Blob([exportHarness(harness.name, harness.description, draft!)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${harness.name}.harness.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function handleImport(file: File): Promise<void> {
    try {
      setDraft(parseImport(await file.text()));
      setErrors({});
      toast.success('Imported into the draft: review it, then save or publish');
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not read that file');
    }
  }

  const bound = harness.bind?.bind_status === 'bound';
  const status = view.published_version == null
    ? 'Not published yet'
    : dirty || view.unpublished_changes
      ? `Version ${view.published_version} published · draft has changes`
      : `Version ${view.published_version} published`;

  return (
    <div className="mt-6 grid gap-6 lg:grid-cols-[1fr_18rem]">
      <section className="rounded-2xl border border-slate-200 bg-white shadow-sm">
        <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-4 py-3">
          <p data-testid="manifest-status" className="mr-auto text-sm text-slate-600">{status}</p>
          <input
            ref={fileInput}
            type="file"
            accept="application/json,.json"
            className="hidden"
            data-testid="import-file"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void handleImport(file);
              e.target.value = '';
            }}
          />
          <button type="button" onClick={() => fileInput.current?.click()} className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm text-slate-600 hover:bg-slate-50">
            <UploadSimple size={16} /> Import
          </button>
          <button type="button" onClick={handleExport} className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm text-slate-600 hover:bg-slate-50">
            <DownloadSimple size={16} /> Export
          </button>
          <button
            type="button"
            onClick={() => void saveDraft().then((ok) => ok && toast.success('Draft saved'))}
            disabled={!dirty || save.isPending}
            className="rounded-lg border border-slate-200 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            {save.isPending ? 'Saving…' : 'Save draft'}
          </button>
          <button
            type="button"
            onClick={() => void handlePublish()}
            disabled={publish.isPending || save.isPending}
            className="inline-flex items-center gap-1.5 rounded-lg bg-gradient-to-br from-primary-500 to-primary-600 px-3 py-2 text-sm font-medium text-white shadow-sm disabled:opacity-60"
          >
            <RocketLaunch size={16} /> {publish.isPending ? 'Publishing…' : 'Publish'}
          </button>
          {bound ? (
            <Link href={`/harness/${harness.id}/test`} className="inline-flex items-center gap-1.5 rounded-lg bg-slate-900 px-3 py-2 text-sm font-medium text-white">
              <Play size={16} weight="fill" /> Test
            </Link>
          ) : null}
        </div>

        <div role="tablist" aria-label="Manifest sections" className="flex gap-1 overflow-x-auto border-b border-slate-100 px-3">
          {SECTIONS.map((s) => (
            <button
              key={s}
              role="tab"
              type="button"
              aria-selected={tab === s}
              onClick={() => setTab(s)}
              className={cn(
                'relative -mb-px border-b-2 px-3 py-2.5 text-sm font-medium',
                tab === s ? 'border-primary-500 text-primary-700' : 'border-transparent text-slate-500 hover:text-slate-700'
              )}
            >
              {TAB_LABEL[s]}
              {errors[s]?.length ? (
                <span className="ml-1.5 rounded-full bg-red-500 px-1.5 text-[10px] font-bold text-white">{errors[s]!.length}</span>
              ) : null}
            </button>
          ))}
        </div>

        <div role="tabpanel" className="p-5">
          <Errors messages={errors.general} />
          <Errors messages={errors[tab]} />
          {tab === 'prompt' && <PromptTab m={draft} onChange={setDraft} />}
          {tab === 'tools' && <ToolsTab m={draft} onChange={setDraft} tools={tools} />}
          {tab === 'model' && <ModelTab m={draft} onChange={setDraft} models={models} />}
          {tab === 'sandbox' && <SandboxTab m={draft} onChange={setDraft} />}
          {tab === 'memory' && <MemoryTab m={draft} onChange={setDraft} />}
          {tab === 'policy' && <PolicyTab m={draft} onChange={setDraft} />}
        </div>
      </section>

      <aside className="space-y-4">
        <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
          <h2 className="text-sm font-semibold text-slate-900">
            {latest ? `Changes since version ${latest.version}` : 'Changes'}
          </h2>
          {!latest ? (
            <p className="mt-2 text-xs text-slate-500">Publish to create version 1.</p>
          ) : sinceLatest.length === 0 ? (
            <p className="mt-2 text-xs text-slate-500">None: the draft is what version {latest.version} runs.</p>
          ) : (
            <ul data-testid="changes" className="mt-2 space-y-1 text-xs">
              {sinceLatest.map((c) => (
                <li key={c.path} className="font-mono text-slate-700">{c.path}</li>
              ))}
            </ul>
          )}
        </div>
        <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
          <h2 className="text-sm font-semibold text-slate-900">Versions</h2>
          {!releases?.length ? (
            <p className="mt-2 text-xs text-slate-500">Nothing published yet.</p>
          ) : (
            <ol data-testid="releases" className="mt-2 space-y-2">
              {releases.map((r, i) => (
                <li key={r.version} className="text-xs">
                  <p className="font-medium text-slate-800">
                    Version {r.version}
                    {i === 0 ? <span className="ml-1.5 rounded-full bg-secondary-100 px-1.5 text-[10px] text-secondary-700">live</span> : null}
                  </p>
                  <p className="text-slate-500">
                    {new Date(r.created_at).toLocaleString()} · {r.manifest.model}
                  </p>
                  <p className="truncate font-mono text-[10px] text-slate-400" title={r.composition_digest}>
                    {r.composition_digest}
                  </p>
                  {releases[i + 1] ? (
                    <p className="text-slate-500">
                      changed: {diffManifests(releases[i + 1].manifest, r.manifest).map((c) => c.path).join(', ') || 'nothing'}
                    </p>
                  ) : null}
                </li>
              ))}
            </ol>
          )}
        </div>
      </aside>
    </div>
  );
}
