import { cn } from '@/lib/utils';

/**
 * A status told by shape as well as colour (the design's rule): running is a
 * solid dot, done a ring, failed a diamond, stopped a dash; only "needs you"
 * (awaiting approval) uses the accent.
 */
export type StatusKind = 'running' | 'awaiting' | 'done' | 'failed' | 'stopped' | 'paused' | 'idle';

const labels: Record<StatusKind, string> = {
  running: 'Running',
  awaiting: 'Awaiting approval',
  done: 'Completed',
  failed: 'Failed',
  stopped: 'Stopped',
  paused: 'Paused',
  idle: 'Idle',
};

function Mark({ kind }: { kind: StatusKind }): JSX.Element {
  switch (kind) {
    case 'running':
      return <span className="h-[7px] w-[7px] rounded-full bg-ink" />;
    case 'awaiting':
      return <span className="h-[7px] w-[7px] rounded-full bg-accent-500" />;
    case 'done':
      return <span className="box-border h-[7px] w-[7px] rounded-full border-[1.5px] border-ink-2" />;
    case 'failed':
      return <span className="h-2 w-2 rotate-45 bg-bad" />;
    case 'paused':
      return (
        <span className="flex gap-[2px]">
          <span className="h-2 w-[2px] bg-ink-2" />
          <span className="h-2 w-[2px] bg-ink-2" />
        </span>
      );
    case 'stopped':
    case 'idle':
    default:
      return <span className="h-[2px] w-2 bg-ink-3" />;
  }
}

interface StatusPillProps {
  kind: StatusKind;
  label?: string;
  className?: string;
}

export function StatusPill({ kind, label, className }: StatusPillProps): JSX.Element {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 whitespace-nowrap text-sm',
        kind === 'awaiting' && 'rounded-full bg-accent-50 px-2.5 py-0.5 font-medium text-accent-700',
        kind === 'failed' && 'font-medium text-ink',
        (kind === 'done' || kind === 'stopped' || kind === 'idle' || kind === 'paused') && 'text-ink-2',
        className,
      )}
    >
      <Mark kind={kind} />
      {label ?? labels[kind]}
    </span>
  );
}

/** The platform's session statuses, mapped onto the shapes above. */
export function sessionStatusKind(status: string | null | undefined, busy?: boolean | null): StatusKind {
  switch (status) {
    case 'awaiting_approval':
      return 'awaiting';
    case 'active':
      return busy ? 'running' : 'idle';
    case 'running':
    case 'starting':
      return 'running';
    case 'paused':
      return 'paused';
    case 'failed':
    case 'budget_exceeded':
    case 'error':
      return 'failed';
    case 'completed':
    case 'succeeded':
      return 'done';
    case 'terminated':
    case 'aborted':
    case 'cancelled':
    case 'canceled':
      return 'stopped';
    default:
      return 'idle';
  }
}
