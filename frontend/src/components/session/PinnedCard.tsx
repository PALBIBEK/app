'use client';

/**
 * "Runs on": the exact versions a session pinned when it started, part by part.
 *
 * A session keeps its pins for life: republishing the agent changes new sessions, not this
 * one. A part deprecated since is still what this session runs, and is marked so.
 */

import { Card, Mono, shortHash } from '@/components/ui';
import { useSessionPins } from '@/lib/api/hooks/useSessions';
import type { PinnedPart } from '@/lib/api/types/catalog.types';
import { cn, formatCount } from '@/lib/utils';

const PROVIDER: Record<string, string> = {
  none: 'No sandbox',
  local_process: 'Local process',
  local_docker: 'Docker',
  e2b: 'E2B',
  runner: 'Your runner',
};

function str(v: unknown): string | null {
  return typeof v === 'string' && v ? v : null;
}

function num(v: unknown): number | null {
  return typeof v === 'number' ? v : null;
}

function idle(seconds: number | null): string | null {
  if (seconds === null) return null;
  if (seconds === 0) return 'never stops when idle';
  return seconds < 60 ? `stops after ${seconds}s idle` : `stops after ${Math.round(seconds / 60)} min idle`;
}

/** The settings of a part, as a line a person reads. */
function describe(part: PinnedPart): React.ReactNode {
  const s = part.settings;
  switch (part.kind) {
    case 'prompt_pack': {
      const n = num(s.sections) ?? 0;
      return [str(s.persona), `${n} section${n === 1 ? '' : 's'}`].filter(Boolean).join(' · ');
    }
    case 'tool_set': {
      const tools = Array.isArray(s.tools) ? (s.tools as { name: string }[]) : [];
      return (
        <span className="flex flex-wrap gap-1">
          {tools.map((t) => (
            <Mono key={t.name} chip className="text-[11.5px]">
              {t.name}
            </Mono>
          ))}
        </span>
      );
    }
    case 'sandbox_profile':
      return [
        PROVIDER[str(s.provider) ?? ''] ?? str(s.provider),
        str(s.runner) ? `on ${str(s.runner)}` : null,
        str(s.file_effects)?.replace(/_/g, ' '),
        s.approval_default === 'ask' ? 'asks before acting' : null,
        s.start === 'with_run' ? 'starts with each run' : s.start === 'on_first_tool' ? 'starts on first tool' : null,
        idle(num(s.idle_stop_s)),
        s.sticky_eligible === false ? 'not kept between turns' : null,
      ]
        .filter(Boolean)
        .join(' · ');
    case 'memory_profile':
      return [
        s.compaction_enabled ? `compacts${num(s.context_window) ? ` within ${formatCount(num(s.context_window))} tokens` : ''}` : 'no compaction',
        s.recall_enabled
          ? `recall: ${str(s.recall_backend)?.replace(/_/g, ' ')}${num(s.recall_max_tokens) ? ` (≤ ${formatCount(num(s.recall_max_tokens))} tokens)` : ''}`
          : null,
      ]
        .filter(Boolean)
        .join(' · ');
    default:
      return null;
  }
}

function label(part: PinnedPart): string {
  if (part.role === 'prompt') return 'Prompt';
  if (part.role === 'tools') return 'Tools';
  if (part.role === 'memory') return 'Memory';
  if (part.role === 'hand:default') return 'Sandbox';
  return `Hand · ${part.role.replace(/^hand:/, '')}`;
}

function Status({ status }: { status: string }): JSX.Element | null {
  if (status === 'offered') return null;
  return (
    <span className={cn('rounded-full px-1.5 py-px text-[11px]', status === 'deprecated' ? 'bg-accent-50 text-accent-700' : 'bg-line-soft text-ink-2')}>
      {status}
    </span>
  );
}

export function PinnedCard({ sessionId }: { sessionId: string }): JSX.Element | null {
  const { data: pins, isLoading, isError } = useSessionPins(sessionId);
  if (isError || (!isLoading && !pins)) return null; // a bare harness pins no composition
  return (
    <Card className="flex flex-col gap-3 px-[18px] py-4" aria-label="What this session runs" data-testid="pinned-card">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-sm font-semibold">Runs on</h2>
        {pins ? (
          <span className="flex items-center gap-1.5 text-[12.5px] text-ink-3">
            {pins.release_version != null ? `version ${pins.release_version}` : pins.composition_semver}
            <Status status={pins.composition_status} />
          </span>
        ) : null}
      </div>
      {!pins ? (
        <p className="text-[13px] text-ink-2">Loading…</p>
      ) : (
        <>
          {pins.composition_status === 'deprecated' ? (
            <p className="text-[12.5px] text-ink-2">
              This version has been retired since; the session keeps running on it. New sessions start on the newest.
            </p>
          ) : null}
          <dl className="flex flex-col gap-2.5">
            {pins.parts.map((part) => (
              <div key={`${part.role}-${part.digest}`} className="flex flex-col gap-0.5" data-testid="pinned-part" data-role={part.role}>
                <dt className="flex items-center gap-2 text-xs text-ink-3">
                  <span>{label(part)}</span>
                  <Mono copy={part.digest} title={part.digest} className="text-[11px] text-ink-3">
                    {part.semver ? `${part.semver} · ` : ''}
                    {shortHash(part.digest)}
                  </Mono>
                  <Status status={part.status} />
                </dt>
                <dd className="text-[13px] text-ink">{describe(part)}</dd>
              </div>
            ))}
            <div className="flex flex-col gap-0.5">
              <dt className="text-xs text-ink-3">Model</dt>
              <dd className="font-mono text-[12.5px]">{pins.model_route ?? 'the deployment’s default'}</dd>
            </div>
          </dl>
        </>
      )}
    </Card>
  );
}
