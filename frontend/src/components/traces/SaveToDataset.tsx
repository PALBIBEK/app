'use client';

/**
 * Save runs to one of the agent's datasets (or a new one): a single run with what a good answer
 * does (``SaveToDataset``), or every recent run of an issue (``SaveIssueToDataset``). The runs
 * are then replayed on later versions, so a fix is checked against the failures it targets.
 */

import { useState, type ReactNode } from 'react';
import { Button } from '@/components/ui';
import Link from 'next/link';

import { useAddIssue, useAddRun, useCreateDataset, useDatasets, usePropose } from '@/lib/api/hooks/useEvaluations';
import type { Issue, RunTrace } from '@/lib/api/types/trace.types';

const NEW = '__new__';

interface DatasetFormProps {
  agentId: string;
  label: string;
  /** Save to the chosen dataset (created first when it is a new one). */
  save: (datasetId: string) => Promise<unknown>;
  saving: boolean;
  close: () => void;
  children?: ReactNode;
}

function DatasetForm({ agentId, label, save, saving, close, children }: DatasetFormProps): JSX.Element {
  const { data: datasets } = useDatasets(agentId);
  const [choice, setChoice] = useState('');
  const [name, setName] = useState('');
  const create = useCreateDataset(agentId);
  const picked = choice || (datasets?.length ? datasets[0].id : NEW);
  const busy = create.isPending || saving;
  return (
    <form
      className="flex w-full flex-col gap-2.5 rounded-lg border border-line bg-canvas p-3"
      aria-label={label}
      onSubmit={async (e) => {
        e.preventDefault();
        let datasetId = picked;
        if (picked === NEW) {
          if (!name.trim()) return;
          datasetId = (await create.mutateAsync({ name: name.trim() })).id;
        }
        await save(datasetId);
        close();
      }}
    >
      <div className="flex flex-wrap gap-2.5">
        <label className="flex min-w-0 flex-[1_1_200px] flex-col gap-1 text-[13px]">
          <span className="text-ink-2">Dataset</span>
          <select value={picked} onChange={(e) => setChoice(e.target.value)} className="h-9 rounded-lg border border-line-strong bg-surface px-2">
            {(datasets ?? []).map((d) => (
              <option key={d.id} value={d.id}>
                {d.name} ({d.items})
              </option>
            ))}
            <option value={NEW}>A new dataset…</option>
          </select>
        </label>
        {picked === NEW ? (
          <label className="flex min-w-0 flex-[1_1_200px] flex-col gap-1 text-[13px]">
            <span className="text-ink-2">Name</span>
            <input value={name} onChange={(e) => setName(e.target.value)} maxLength={120} placeholder="regressions" className="h-9 rounded-lg border border-line-strong bg-surface px-2.5" />
          </label>
        ) : null}
      </div>
      {children}
      <div className="flex gap-2">
        <Button type="submit" size="sm" variant="primary" disabled={busy || (picked === NEW && !name.trim())}>
          Save
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={close}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

export function SaveToDataset({ trace }: { trace: RunTrace }): JSX.Element | null {
  const agentId = trace.agent_id ?? '';
  const [open, setOpen] = useState(false);
  const [expected, setExpected] = useState('');
  const add = useAddRun();
  if (!agentId) return null;
  if (!open) {
    return (
      <Button size="sm" onClick={() => setOpen(true)} data-testid="save-to-dataset">
        Save to a dataset
      </Button>
    );
  }
  return (
    <DatasetForm
      agentId={agentId}
      label="Save this run to a dataset"
      saving={add.isPending}
      close={() => {
        setOpen(false);
        setExpected('');
      }}
      save={(datasetId) =>
        add.mutateAsync({ datasetId, session_id: trace.session_id, run_id: trace.run_id, expected: expected.trim() || null })
      }
    >
      <label className="flex flex-col gap-1 text-[13px]">
        <span className="text-ink-2">What a good answer does (optional)</span>
        <textarea value={expected} onChange={(e) => setExpected(e.target.value)} rows={2} maxLength={4000} className="rounded-lg border border-line-strong bg-surface px-2.5 py-1.5" />
      </label>
    </DatasetForm>
  );
}

export function SaveIssueToDataset({ issue }: { issue: Issue }): JSX.Element | null {
  const [open, setOpen] = useState(false);
  const add = useAddIssue();
  if (!issue.agent_id) return null;
  if (!open) {
    return (
      <Button size="sm" variant="ghost" onClick={() => setOpen(true)} data-testid="save-issue-to-dataset">
        Save its runs to a dataset
      </Button>
    );
  }
  return (
    <DatasetForm
      agentId={issue.agent_id}
      label="Save this issue's runs to a dataset"
      saving={add.isPending}
      close={() => setOpen(false)}
      save={(datasetId) => add.mutateAsync({ datasetId, issueKey: issue.key })}
    >
      <p className="text-xs text-ink-3">
        Its latest runs are saved (never an experiment&rsquo;s replays). One saved run in three is held out: a proposed change
        never reads it, so it shows whether the change fixes more than what it learnt from.
      </p>
    </DatasetForm>
  );
}

/** Start a change for one issue: its runs are saved to the dataset it is measured on, and the
 * reflection reads that issue first. */
export function ProposeFromIssue({ issue }: { issue: Issue }): JSX.Element | null {
  const [open, setOpen] = useState(false);
  const propose = usePropose(issue.agent_id ?? '');
  const [started, setStarted] = useState<string | null>(null);
  if (!issue.agent_id) return null;
  if (started) {
    return (
      <Link href={`/traces/improve/${started}`} className="text-sm text-accent-600 hover:underline" data-testid="proposal-started">
        A change for this issue is being worked on
      </Link>
    );
  }
  if (!open) {
    return (
      <Button size="sm" variant="ghost" onClick={() => setOpen(true)} data-testid="propose-from-issue">
        Propose a fix
      </Button>
    );
  }
  return (
    <DatasetForm
      agentId={issue.agent_id}
      label="Propose a fix for this issue"
      saving={propose.isPending}
      close={() => setOpen(false)}
      save={async (datasetId) => {
        const p = await propose.mutateAsync({ dataset_id: datasetId, issue_key: issue.key, notes: `Fix: ${issue.title}` });
        setStarted(p.id);
      }}
    >
      <p className="text-xs text-ink-3">
        Its runs are saved to the dataset, and a change is learnt for this issue first, measured on the dataset and gated.
        It goes live only if you approve it.
      </p>
    </DatasetForm>
  );
}
