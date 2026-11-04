'use client';

/**
 * Propose by itself: when the agent's biggest issue fails enough runs since the last attempt,
 * its runs are saved to the chosen dataset and a change is proposed, measured and gated. A
 * person still approves anything that goes live.
 */

import { useEffect, useState } from 'react';

import { Button, Card, CardHeader, Select } from '@/components/ui';
import { useAutoImprove, useSaveAutoImprove } from '@/lib/api/hooks/useEvaluations';
import type { DatasetSummary } from '@/lib/api/types/evaluation.types';
import { formatRelativeTime } from '@/lib/utils';

const COOLDOWNS = [
  { value: '60', label: 'an hour' },
  { value: '360', label: '6 hours' },
  { value: '1440', label: 'a day' },
  { value: '10080', label: 'a week' },
];

export function AutoImproveCard({ agentId, datasets }: { agentId: string; datasets: DatasetSummary[] }): JSX.Element | null {
  const { data: current } = useAutoImprove(agentId || undefined);
  const save = useSaveAutoImprove(agentId);
  const [dataset, setDataset] = useState('');
  const [minRuns, setMinRuns] = useState('5');
  const [cooldown, setCooldown] = useState('360');
  useEffect(() => {
    setDataset(current?.dataset_id ?? datasets[0]?.id ?? '');
    setMinRuns(String(current?.min_runs ?? 5));
    setCooldown(String(current?.cooldown_minutes ?? 360));
  }, [current, datasets]);
  if (!agentId || !datasets.length) return null;
  const on = current?.enabled ?? false;
  const runs = Number(minRuns);
  const body = (enabled: boolean) => ({
    dataset_id: dataset,
    enabled,
    min_runs: runs,
    cooldown_minutes: Number(cooldown),
    rounds: current?.rounds ?? 1,
  });
  return (
    <Card>
      <CardHeader
        title="Propose changes by itself"
        description="When one issue fails enough runs since the last attempt, its runs are saved to the dataset and a change is proposed. It still has to pass the gate and your approval."
      />
      <div className="flex flex-col gap-3 border-t border-line-soft px-5 py-4" data-testid="auto-improve" data-enabled={on}>
        <div className="flex flex-wrap items-end gap-3">
          <Select label="Measured on" value={dataset} onChange={setDataset} options={datasets.map((d) => ({ value: d.id, label: d.name }))} />
          <label className="flex w-[180px] flex-col gap-1 text-sm">
            <span className="text-ink-2">Failing runs that start one</span>
            <input
              type="number"
              min={2}
              max={500}
              value={minRuns}
              onChange={(e) => setMinRuns(e.target.value)}
              className="tabular h-10 rounded-lg border border-line-strong bg-surface px-3"
            />
          </label>
          <Select label="At most once every" value={cooldown} onChange={setCooldown} options={COOLDOWNS} />
          <Button
            variant={on ? 'secondary' : 'primary'}
            disabled={!dataset || !(runs >= 2 && runs <= 500) || save.isPending}
            onClick={() => save.mutate(body(!on))}
            data-testid="auto-improve-toggle"
          >
            {on ? 'Turn off' : 'Turn on'}
          </Button>
          {on ? (
            <Button variant="ghost" disabled={save.isPending} onClick={() => save.mutate(body(true))}>
              Save
            </Button>
          ) : null}
        </div>
        {current?.last_note ? (
          <p className="text-[13px] text-ink-2" data-testid="auto-improve-note">
            {current.last_attempt_at ? `Last attempt ${formatRelativeTime(new Date(current.last_attempt_at))}. ` : ''}
            {current.last_note}.
          </p>
        ) : null}
      </div>
    </Card>
  );
}
