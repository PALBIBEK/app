'use client';

import { Check, Copy } from '@phosphor-icons/react';
import { useState } from 'react';

import { cn } from '@/lib/utils';

/** Page title, one line on what the page is for, and its main actions. */
export function PageHeader({
  title,
  description,
  actions,
  eyebrow,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  eyebrow?: React.ReactNode;
}): JSX.Element {
  return (
    <div className="flex flex-wrap items-end gap-x-6 gap-y-3">
      <div className="min-w-0 flex-[1_1_320px]">
        {eyebrow ? <div className="mb-1 text-sm text-ink-3">{eyebrow}</div> : null}
        <h1 className="text-2xl font-semibold tracking-[-0.02em]">{title}</h1>
        {description ? <p className="mt-1 text-ink-2">{description}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}

/** A number with its label; `accent` when it asks for action. */
export function StatTile({
  label,
  value,
  hint,
  tone = 'default',
}: {
  label: string;
  value: React.ReactNode;
  hint?: React.ReactNode;
  tone?: 'default' | 'accent';
}): JSX.Element {
  return (
    <div
      className={cn(
        'rounded-card border px-4 py-3.5',
        tone === 'accent' ? 'border-accent-200 bg-accent-50' : 'border-line bg-surface',
      )}
    >
      <div className={cn('text-sm', tone === 'accent' ? 'text-accent-700' : 'text-ink-2')}>{label}</div>
      <div className={cn('tabular text-[26px] font-semibold leading-tight tracking-[-0.02em]', tone === 'accent' && 'text-accent-700')}>
        {value}
      </div>
      {hint ? <div className="mt-0.5 text-xs text-ink-3">{hint}</div> : null}
    </div>
  );
}

export function StatGrid({ children }: { children: React.ReactNode }): JSX.Element {
  return <div className="grid grid-cols-[repeat(auto-fit,minmax(200px,1fr))] gap-3">{children}</div>;
}

/** One choice out of a few (status filters): real buttons with aria-pressed. */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: readonly { value: T; label: string; count?: number }[];
  value: T;
  onChange: (value: T) => void;
  label: string;
}): JSX.Element {
  return (
    <div role="group" aria-label={label} className="flex flex-wrap gap-1 rounded-[9px] bg-line-soft p-[3px]">
      {options.map((o) => {
        const on = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(o.value)}
            className={cn(
              'h-[34px] rounded-[7px] px-3 text-sm transition-colors',
              on ? 'bg-surface font-medium text-ink shadow-[0_1px_1px_rgba(0,0,0,0.04)]' : 'text-ink-2 hover:text-ink',
            )}
          >
            {o.label}
            {o.count !== undefined ? <span className="tabular ml-1.5 text-ink-3">{o.count}</span> : null}
          </button>
        );
      })}
    </div>
  );
}

/** An id, hash, version or command in mono; `copy` adds a copy button. */
export function Mono({
  children,
  copy,
  chip,
  className,
  title,
}: {
  children: React.ReactNode;
  copy?: string;
  chip?: boolean;
  className?: string;
  title?: string;
}): JSX.Element {
  const [copied, setCopied] = useState(false);
  const text = (
    <span
      title={title}
      className={cn(
        'font-mono text-[12.5px]',
        chip && 'rounded-md border border-line-soft bg-canvas px-2 py-px',
        className,
      )}
    >
      {children}
    </span>
  );
  if (!copy) return text;
  return (
    <span className="inline-flex items-center gap-1">
      {text}
      <button
        type="button"
        aria-label={copied ? 'Copied' : 'Copy'}
        onClick={() => {
          void navigator.clipboard?.writeText(copy).then(() => {
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1200);
          });
        }}
        className="inline-flex h-7 w-7 items-center justify-center rounded-md text-ink-3 hover:bg-line-soft hover:text-ink"
      >
        {copied ? <Check size={14} /> : <Copy size={14} />}
      </button>
    </span>
  );
}

/** The first and last characters of a long hash, the rest elided. */
export function shortHash(digest: string | null | undefined, keep = 8): string {
  if (!digest) return '';
  const bare = digest.replace(/^sha256:/, '');
  return bare.length <= keep ? bare : `${bare.slice(0, keep)}…`;
}

/** How full something is (context window, budget): the accent once it is nearly full. */
export function Meter({
  value,
  max,
  label,
  warnAt = 0.85,
  className,
}: {
  value: number;
  max: number;
  label: string;
  warnAt?: number;
  className?: string;
}): JSX.Element {
  const share = max > 0 ? Math.min(Math.max(value / max, 0), 1) : 0;
  return (
    <div
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuenow={value}
      className={cn('h-1 rounded-sm bg-line-soft', className)}
    >
      <div
        className={cn('h-1 rounded-sm', share >= warnAt ? 'bg-accent-500' : 'bg-ink')}
        style={{ width: `${(share * 100).toFixed(1)}%` }}
      />
    </div>
  );
}

/** What a list shows when it has nothing yet, and what to do about it. */
export function EmptyState({
  title,
  description,
  action,
  icon,
}: {
  title: string;
  description?: React.ReactNode;
  action?: React.ReactNode;
  icon?: React.ReactNode;
}): JSX.Element {
  return (
    <div className="flex flex-col items-center gap-2 px-6 py-14 text-center">
      {icon ? <div className="mb-1 text-ink-3" aria-hidden="true">{icon}</div> : null}
      <p className="font-medium">{title}</p>
      {description ? <p className="max-w-md text-sm text-ink-2">{description}</p> : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}

/** A failed load, with a retry. */
export function ErrorNote({ message, onRetry }: { message: string; onRetry?: () => void }): JSX.Element {
  return (
    <div role="alert" className="flex flex-wrap items-center gap-3 rounded-card border border-line bg-surface px-4 py-3 text-sm">
      <span className="h-2 w-2 rotate-45 bg-bad" aria-hidden="true" />
      <span className="flex-1">{message}</span>
      {onRetry ? (
        <button type="button" onClick={onRetry} className="font-medium text-accent-600 hover:text-accent-700">
          Try again
        </button>
      ) : null}
    </div>
  );
}

/** Placeholder rows while data loads. */
export function SkeletonRows({ rows = 5 }: { rows?: number }): JSX.Element {
  return (
    <div className="divide-y divide-line-soft" aria-hidden="true">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex items-center gap-4 px-4 py-3.5">
          <div className="h-3 w-1/3 animate-pulse rounded bg-line-soft" />
          <div className="h-3 w-1/6 animate-pulse rounded bg-line-soft" />
          <div className="ml-auto h-3 w-12 animate-pulse rounded bg-line-soft" />
        </div>
      ))}
    </div>
  );
}
