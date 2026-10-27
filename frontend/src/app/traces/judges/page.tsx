'use client';

/**
 * Judges: rubrics a model applies to an agent's finished runs. A person writes what a good
 * run looks like; after each run, on the share of runs chosen, the judge passes or fails it
 * with a reason. Changing a rubric makes a new version, so earlier verdicts keep their meaning.
 */

import { useEffect, useState } from 'react';

import { AppShell, PageBody } from '@/components/layout/AppShell';
import { TracesNav } from '@/components/traces/TracesNav';
import { Button, Card, EmptyState, ErrorNote, PageHeader, Select, SkeletonRows } from '@/components/ui';
import { useHarnesses } from '@/lib/api/hooks/useHarnesses';
import { useDeleteJudge, useJudges, useSaveJudge } from '@/lib/api/hooks/useTraces';
import type { Judge } from '@/lib/api/types/trace.types';
import { formatStamp } from '@/lib/utils';

const EXAMPLE = 'Answers the question that was asked. Changes only the files it needed to, and says which.';

function JudgeForm({ agentId, editing, onDone }: { agentId: string; editing: Judge | null; onDone: () => void }): JSX.Element {
  const [name, setName] = useState(editing?.name ?? '');
  const [rubric, setRubric] = useState(editing?.rubric ?? '');
  const [share, setShare] = useState(String(Math.round((editing?.sample_rate ?? 1) * 100)));
  const [model, setModel] = useState(editing?.model ?? '');
  const save = useSaveJudge(agentId);
  const pct = Number(share);
  const valid = name.trim().length > 0 && rubric.trim().length >= 10 && pct >= 0 && pct <= 100;
  return (
    <form
      className="flex flex-col gap-3"
      aria-label={editing ? `Edit judge ${editing.name}` : 'New judge'}
      onSubmit={(e) => {
        e.preventDefault();
        if (!valid) return;
        save.mutate(
          { name: name.trim(), rubric: rubric.trim(), sample_rate: pct / 100, model: model.trim() || null, enabled: editing?.enabled ?? true },
          { onSuccess: onDone },
        );
      }}
    >
      <div className="flex flex-wrap gap-3">
        <label className="flex min-w-0 flex-[1_1_200px] flex-col gap-1 text-sm">
          <span className="text-ink-2">Name</span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={!!editing}
            maxLength={64}
            placeholder="answers the question"
            className="h-10 rounded-lg border border-line-strong bg-surface px-3 disabled:bg-canvas"
          />
        </label>
        <label className="flex w-[160px] flex-col gap-1 text-sm">
          <span className="text-ink-2">Share of runs judged (%)</span>
          <input
            type="number"
            min={0}
            max={100}
            value={share}
            onChange={(e) => setShare(e.target.value)}
            className="tabular h-10 rounded-lg border border-line-strong bg-surface px-3"
          />
        </label>
        <label className="flex min-w-0 flex-[1_1_220px] flex-col gap-1 text-sm">
          <span className="text-ink-2">Model (blank: the agent&rsquo;s own)</span>
          <input
            value={model}
            onChange={(e) => setModel(e.target.value)}
            maxLength={255}
            className="h-10 rounded-lg border border-line-strong bg-surface px-3 font-mono text-[13px]"
          />
        </label>
      </div>
      <label className="flex flex-col gap-1 text-sm">
        <span className="text-ink-2">What a good run looks like</span>
        <textarea
          value={rubric}
          onChange={(e) => setRubric(e.target.value)}
          rows={4}
          maxLength={4000}
          placeholder={EXAMPLE}
          className="rounded-lg border border-line-strong bg-surface px-3 py-2 text-[14px]"
        />
      </label>
      <div className="flex items-center gap-2">
        <Button type="submit" variant="primary" disabled={!valid || save.isPending}>
          {editing ? 'Save as a new version' : 'Add judge'}
        </Button>
        <Button type="button" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
        {editing ? <span className="text-xs text-ink-3">Earlier verdicts keep the version that gave them.</span> : null}
      </div>
    </form>
  );
}

function JudgeRow({ agentId, judge, onEdit }: { agentId: string; judge: Judge; onEdit: () => void }): JSX.Element {
  const save = useSaveJudge(agentId);
  const remove = useDeleteJudge(agentId);
  return (
    <li className="flex flex-col gap-2 px-5 py-4" data-testid="judge" data-name={judge.name}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="font-semibold">{judge.name}</span>
        <span className="rounded-md bg-violet-50 px-1.5 py-px font-mono text-xs text-violet-800">v{judge.version}</span>
        <span className="text-xs text-ink-3">
          {Math.round(judge.sample_rate * 100)}% of runs · {judge.model ?? "the agent's model"} · changed {formatStamp(judge.updated_ms)}
        </span>
        <span className="flex-1" />
        <Button
          size="sm"
          variant="secondary"
          aria-pressed={judge.enabled}
          onClick={() =>
            save.mutate({ name: judge.name, rubric: judge.rubric, sample_rate: judge.sample_rate, model: judge.model, enabled: !judge.enabled })
          }
        >
          {judge.enabled ? 'On' : 'Off'}
        </Button>
        <Button size="sm" variant="ghost" onClick={onEdit}>
          Edit
        </Button>
        <Button size="sm" variant="ghost" onClick={() => remove.mutate(judge.id)}>
          Remove
        </Button>
      </div>
      <p className="whitespace-pre-wrap text-[13.5px] text-ink-2">{judge.rubric}</p>
    </li>
  );
}

export default function JudgesPage(): JSX.Element {
  const { data: agents } = useHarnesses();
  const published = (agents?.data ?? []).filter((a) => a.bind?.composition_id);
  const [agent, setAgent] = useState('');
  useEffect(() => {
    if (!agent && published.length) setAgent(published[0].id);
  }, [agent, published]);
  const { data, isLoading, error, refetch } = useJudges(agent || undefined);
  const [editing, setEditing] = useState<Judge | 'new' | null>(null);
  useEffect(() => setEditing(null), [agent]);

  return (
    <AppShell>
      <PageBody>
        <PageHeader
          title="Traces"
          description="Every run of every agent: what was asked, what it did, and where the time and money went."
        />
        <TracesNav current="/traces/judges" />
        <div className="flex flex-wrap items-center gap-3">
          <Select label="Agent" value={agent} onChange={setAgent} options={published.map((a) => ({ value: a.id, label: a.name }))} />
          <p className="max-w-2xl text-sm text-ink-2">
            A judge is a rubric a model applies to the agent&rsquo;s finished runs. Its verdicts show on each run and add up to issues.
            Judging costs what the agent&rsquo;s own calls cost.
          </p>
        </div>
        {!published.length ? (
          <Card>
            <EmptyState title="No published agents" description="Publish an agent to give it judges." />
          </Card>
        ) : (
          <Card>
            {error ? <ErrorNote message="Could not load judges." onRetry={() => void refetch()} /> : null}
            {isLoading ? (
              <div className="p-4">
                <SkeletonRows rows={2} />
              </div>
            ) : (
              <ul className="divide-y divide-line-soft" data-testid="judges">
                {(data?.judges ?? []).map((j) =>
                  editing !== 'new' && editing?.id === j.id ? (
                    <li key={j.id} className="px-5 py-4">
                      <JudgeForm agentId={agent} editing={j} onDone={() => setEditing(null)} />
                    </li>
                  ) : (
                    <JudgeRow key={j.id} agentId={agent} judge={j} onEdit={() => setEditing(j)} />
                  ),
                )}
                <li className="px-5 py-4">
                  {editing === 'new' ? (
                    <JudgeForm agentId={agent} editing={null} onDone={() => setEditing(null)} />
                  ) : (
                    <Button onClick={() => setEditing('new')}>Add a judge</Button>
                  )}
                </li>
              </ul>
            )}
          </Card>
        )}
      </PageBody>
    </AppShell>
  );
}
