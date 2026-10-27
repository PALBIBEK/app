'use client';

/** The views of traces: every run, the issues they add up to, the judges, the datasets of
 * saved runs that versions are compared on, and the changes learned from them. */

import Link from 'next/link';

import { cn } from '@/lib/utils';

const VIEWS = [
  { href: '/traces', label: 'Runs' },
  { href: '/traces/issues', label: 'Issues' },
  { href: '/traces/judges', label: 'Judges' },
  { href: '/traces/datasets', label: 'Datasets' },
  { href: '/traces/improve', label: 'Improve' },
] as const;

export function TracesNav({ current }: { current: (typeof VIEWS)[number]['href'] }): JSX.Element {
  return (
    <nav aria-label="Traces views" className="flex gap-1 border-b border-line">
      {VIEWS.map((v) => (
        <Link
          key={v.href}
          href={v.href}
          aria-current={v.href === current ? 'page' : undefined}
          className={cn(
            '-mb-px border-b-2 px-3.5 py-2.5 text-[13.5px]',
            v.href === current ? 'border-accent-500 font-semibold text-ink' : 'border-transparent text-ink-2 hover:text-ink',
          )}
        >
          {v.label}
        </Link>
      ))}
    </nav>
  );
}
