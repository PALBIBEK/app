'use client';

import { MagnifyingGlass } from '@phosphor-icons/react';
import { useId } from 'react';

import { cn } from '@/lib/utils';

export function SearchInput({
  value,
  onChange,
  placeholder,
  label,
  className,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  label: string;
  className?: string;
}): JSX.Element {
  const id = useId();
  return (
    <div className={cn('relative min-w-0 flex-[1_1_280px] sm:max-w-[420px]', className)}>
      <label htmlFor={id} className="sr-only">
        {label}
      </label>
      <MagnifyingGlass size={16} className="pointer-events-none absolute left-3 top-3 text-ink-3" aria-hidden="true" />
      <input
        id={id}
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="h-10 w-full rounded-lg border border-line-strong bg-surface pl-9 pr-3 text-base text-ink placeholder:text-ink-3"
      />
    </div>
  );
}

/** A labelled select in the same style. */
export function Select({
  label,
  value,
  onChange,
  options,
  hideLabel,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: readonly { value: string; label: string }[];
  hideLabel?: boolean;
}): JSX.Element {
  const id = useId();
  return (
    <div className="flex items-center gap-2">
      <label htmlFor={id} className={cn('text-sm text-ink-2', hideLabel && 'sr-only')}>
        {label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="h-10 rounded-lg border border-line-strong bg-surface px-2.5 text-base text-ink"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </div>
  );
}
