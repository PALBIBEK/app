'use client';

/**
 * Save this run to one of the agent's datasets (or a new one), with what a good answer does:
 * the run is then replayed on later versions, so a fix is checked against it.
 */

import { useState } from 'react';

import { Button } from '@/components/ui';
import { useAddRun, useCreateDataset, useDatasets } from '@/lib/api/hooks/useEvaluations';
import type { RunTrace } from '@/lib/api/types/trace.types';

const NEW = '__new__';

export function SaveToDataset({ trace }: { trace: RunTrace }): JSX.Element | null {
  const agentId = trace.agent_id ?? '';
  const [open, setOpen] = useState(false);
  const { data: datasets } = useDatasets(open ? agentId : undefined);
  const [choice, setChoice] = useState('');
  const [name, setName] = useState('');
  const [expected, setExpected] = useState('');
  const create = useCreateDataset(agentId);
  const add = useAddRun();
  if (!agentId) return null;
  const picked = choice || (datasets?.length ? datasets[0].id : NEW);
  const busy = create.isPending || add.isPending;

  if (!open) {
    return (
      <Button size="sm" onClick={() => setOpen(true)} data-testid="save-to-dataset">
        Save to a dataset
      </Button>
    );
  }
  return (
    <form
      className="flex w-full flex-col gap-2.5 rounded-lg border border-line bg-canvas p-3"
      aria-label="Save this run to a dataset"
      onSubmit={async (e) => {
        e.preventDefault();
        let datasetId = picked;
        if (picked === NEW) {
          if (!name.trim()) return;
          datasetId = (await create.mutateAsync({ name: name.trim() })).id;
        }
        await add.mutateAsync({ datasetId, session_id: trace.session_id, run_id: trace.run_id, expected: expected.trim() || null });
        setOpen(false);
        setExpected('');
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
      <label className="flex flex-col gap-1 text-[13px]">
        <span className="text-ink-2">What a good answer does (optional)</span>
        <textarea value={expected} onChange={(e) => setExpected(e.target.value)} rows={2} maxLength={4000} className="rounded-lg border border-line-strong bg-surface px-2.5 py-1.5" />
      </label>
      <div className="flex gap-2">
        <Button type="submit" size="sm" variant="primary" disabled={busy || (picked === NEW && !name.trim())}>
          Save
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
