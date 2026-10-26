'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { isAxiosError } from 'axios';
import { Check, DownloadSimple, ListChecks, Play, RocketLaunch, UploadSimple } from '@phosphor-icons/react';
import { toast } from 'sonner';

import {
  useCatalogModels,
  useCatalogTools,
  useCheckDraft,
  useManifest,
  usePublishHarness,
  useReleases,
  useSaveManifest,
} from '@/lib/api/hooks/useManifest';
import type { Harness } from '@/lib/api/types/harness.types';
import { SECTIONS, type Manifest, type Release, type Section } from '@/lib/api/types/manifest.types';
import { diffManifests, errorsBySection, exportHarness, parseImport, type SectionErrors } from '@/lib/harness/manifest';
import { Button, Card, LinkButton, Mono } from '@/components/ui';
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
  const check = useCheckDraft(harness.id);

  const [draft, setDraft] = useState<Manifest | null>(null);
  const [tab, setTab] = useState<Section>('prompt');
  const [showPassed, setShowPassed] = useState(false);
  const [errors, setErrors] = useState<SectionErrors>({});
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (view && draft === null) setDraft(view.manifest);
  }, [view, draft]);

  const dirty = useMemo(() => Boolean(view && draft && diffManifests(view.manifest, draft).length), [view, draft]);
  const latest: Release | undefined = releases?.[0];
  const sinceLatest = useMemo(() => (latest && draft ? diffManifests(latest.manifest, draft) : []), [latest, draft]);

  if (isLoading || !draft || !view) {
    return <p className="text-sm text-ink-2">Loading the manifest…</p>;
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
      check.reset();
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

  async function runChecks(): Promise<void> {
    if (dirty && !(await saveDraft())) return;
    try {
      await check.mutateAsync();
    } catch (error) {
      setErrors(apiErrors(error));
    }
  }

  const bound = harness.bind?.bind_status === 'bound';
  const status = view.published_version == null
    ? 'Not published yet'
    : dirty || view.unpublished_changes
      ? `Version ${view.published_version} published · draft has changes`
      : `Version ${view.published_version} published`;
  const failedBySection = new Map<string, number>();
  for (const c of check.data?.checks ?? []) if (!c.ok) failedBySection.set(c.section, (failedBySection.get(c.section) ?? 0) + 1);

  return (
    <div className="flex flex-wrap items-start gap-5">
      <Card className="min-w-0 flex-[999_1_640px]">
        <div className="flex flex-wrap items-center gap-2 border-b border-line-soft px-4 py-3">
          <p data-testid="manifest-status" className="mr-auto flex items-center gap-2 text-sm text-ink-2">
            <span
              aria-hidden="true"
              className={cn(
                'h-2 w-2 rounded-full',
                view.published_version == null ? 'box-border border-[1.5px] border-ink' : dirty || view.unpublished_changes ? 'bg-accent-500' : 'bg-ink',
              )}
            />
            {status}
          </p>
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
          <Button variant="ghost" size="sm" onClick={() => fileInput.current?.click()}>
            <UploadSimple size={15} /> Import
          </Button>
          <Button variant="ghost" size="sm" onClick={handleExport}>
            <DownloadSimple size={15} /> Export
          </Button>
          <Button size="sm" onClick={() => void saveDraft().then((ok) => ok && toast.success('Draft saved'))} disabled={!dirty || save.isPending}>
            {save.isPending ? 'Saving…' : 'Save draft'}
          </Button>
          <Button size="sm" onClick={() => void runChecks()} disabled={check.isPending || save.isPending}>
            <ListChecks size={15} /> {check.isPending ? 'Checking…' : 'Check'}
          </Button>
          <Button variant="primary" size="sm" onClick={() => void handlePublish()} disabled={publish.isPending || save.isPending}>
            <RocketLaunch size={15} /> {publish.isPending ? 'Publishing…' : 'Publish'}
          </Button>
          {bound ? (
            <LinkButton href={`/harness/${harness.id}/test`} size="sm" className="bg-ink text-white hover:bg-ink/90">
              <Play size={14} weight="fill" /> Test
            </LinkButton>
          ) : null}
        </div>

        <div role="tablist" aria-label="Manifest sections" className="flex gap-1 overflow-x-auto border-b border-line-soft px-3">
          {SECTIONS.map((s) => {
            const n = (errors[s]?.length ?? 0) + (failedBySection.get(s) ?? 0);
            return (
              <button
                key={s}
                role="tab"
                type="button"
                aria-selected={tab === s}
                onClick={() => setTab(s)}
                className={cn(
                  'relative -mb-px whitespace-nowrap border-b-2 px-3 py-2.5 text-sm font-medium',
                  tab === s ? 'border-accent-500 text-ink' : 'border-transparent text-ink-2 hover:text-ink'
                )}
              >
                {TAB_LABEL[s]}
                {n ? <span className="ml-1.5 rounded-full bg-accent-500 px-1.5 text-[10px] font-semibold text-white">{n}</span> : null}
              </button>
            );
          })}
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
      </Card>

      <aside className="flex min-w-0 flex-[1_1_300px] flex-col gap-4">
        <Card className="px-[18px] py-4" data-testid="publish-checks">
          <div className="flex items-baseline justify-between gap-2">
            <h2 className="text-sm font-semibold">Publish checks</h2>
            {check.data ? (
              <span className={cn('text-[12.5px]', check.data.ok ? 'text-ink-2' : 'font-medium text-accent-700')}>
                {check.data.ok ? 'All pass' : `${check.data.checks.filter((c) => !c.ok).length} to fix`}
              </span>
            ) : null}
          </div>
          {!check.data ? (
            <p className="mt-2 text-[13px] text-ink-2">
              See what publishing would find, every problem at once, without publishing anything.
            </p>
          ) : (
            <>
              <ul className="mt-2.5 flex flex-col gap-2">
                {check.data.checks
                  .filter((c) => !c.ok || showPassed)
                  .map((c, i) => (
                    <li key={i} className="flex gap-2 text-[13px]">
                      {c.ok ? (
                        <Check size={14} weight="bold" className="mt-[3px] shrink-0 text-ink-2" aria-label="passed" />
                      ) : (
                        <span className="mt-[6px] h-2 w-2 shrink-0 rotate-45 bg-accent-500" aria-label="failed" />
                      )}
                      <span className="min-w-0">
                        <button
                          type="button"
                          onClick={() => SECTIONS.includes(c.section as Section) && setTab(c.section as Section)}
                          className={cn('text-left', c.ok ? 'text-ink-2' : 'font-medium text-ink hover:text-accent-700')}
                        >
                          {c.check}
                        </button>
                        {!c.ok && c.message ? <span className="block break-words text-xs text-ink-2">{c.message}</span> : null}
                      </span>
                    </li>
                  ))}
              </ul>
              <button type="button" onClick={() => setShowPassed((v) => !v)} className="mt-2.5 text-[12.5px] text-ink-2 hover:text-ink">
                {showPassed ? 'Hide passing checks' : `${check.data.checks.filter((c) => c.ok).length} checks pass · show`}
              </button>
            </>
          )}
        </Card>
        <Card className="px-[18px] py-4">
          <h2 className="text-sm font-semibold">
            {latest ? `Changes since version ${latest.version}` : 'Changes'}
          </h2>
          {!latest ? (
            <p className="mt-2 text-[13px] text-ink-2">Publish to create version 1.</p>
          ) : sinceLatest.length === 0 ? (
            <p className="mt-2 text-[13px] text-ink-2">None: the draft is what version {latest.version} runs.</p>
          ) : (
            <ul data-testid="changes" className="mt-2 flex flex-wrap gap-1.5">
              {sinceLatest.map((c) => (
                <li key={c.path}>
                  <Mono chip className="text-xs">{c.path}</Mono>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card className="px-[18px] py-4">
          <h2 className="text-sm font-semibold">Versions</h2>
          {!releases?.length ? (
            <p className="mt-2 text-[13px] text-ink-2">Nothing published yet.</p>
          ) : (
            <ol data-testid="releases" className="mt-2 flex flex-col">
              {releases.map((r, i) => (
                <li key={r.version} className="flex gap-3 border-t border-line-soft py-2.5 first:border-0">
                  <span
                    aria-hidden="true"
                    className={cn('mt-[5px] block h-2.5 w-2.5 shrink-0 rounded-full', i === 0 ? 'bg-accent-500' : 'box-border border-[1.5px] border-[#8A9099]')}
                  />
                  <div className="min-w-0 text-[13px]">
                    <p className="font-medium">
                      Version {r.version}
                      {i === 0 ? <span className="ml-1.5 rounded-full bg-accent-50 px-[7px] py-px text-[11.5px] font-normal text-accent-700">live</span> : null}
                    </p>
                    <p className="text-xs text-ink-3">
                      {new Date(r.created_at).toLocaleString()} · {r.manifest.model}
                    </p>
                    <p className="truncate font-mono text-[11px] text-ink-3" title={r.composition_digest}>
                      {r.composition_digest}
                    </p>
                    {releases[i + 1] ? (
                      <p className="text-xs text-ink-2">
                        changed: {diffManifests(releases[i + 1].manifest, r.manifest).map((c) => c.path).join(', ') || 'nothing'}
                      </p>
                    ) : null}
                  </div>
                </li>
              ))}
            </ol>
          )}
        </Card>
      </aside>
    </div>
  );
}
