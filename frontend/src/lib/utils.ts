import { clsx, type ClassValue } from 'clsx';
import { extendTailwindMerge } from 'tailwind-merge';

// The design's own tokens (tailwind.config.ts), so merging keeps the right one:
// without them `text-2xs` reads as a colour and would knock out `text-ink`.
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      'font-size': [{ text: ['2xs'] }],
      'text-color': [{ text: ['ink', 'ink-2', 'ink-3', 'good', 'warn', 'bad'] }],
      'border-color': [{ border: ['line', 'line-soft', 'line-strong'] }],
      rounded: [{ rounded: ['card'] }],
    },
  },
});

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

export function formatRelativeTime(iso: string | Date | null | undefined): string {
  if (!iso) return '';
  const date = typeof iso === 'string' ? new Date(iso) : iso;
  const diffMs = Date.now() - date.getTime();
  const sec = Math.round(diffMs / 1000);
  if (sec < 60) return 'just now';
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.round(hr / 24);
  return `${day}d ago`;
}

/** "14m 08s", "2h 05m", "3d 4h": how long something ran. */
export function formatDuration(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return '—';
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, '0')}s`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${String(m % 60).padStart(2, '0')}m`;
  return `${Math.floor(h / 24)}d ${h % 24}h`;
}

/** Dollars: cents for amounts over a dollar, more digits for the small ones a single run costs. */
export function formatMoney(amount: number | null | undefined): string {
  const n = amount ?? 0;
  if (n === 0) return '$0.00';
  if (Math.abs(n) < 0.01) return `$${n.toFixed(4)}`;
  if (Math.abs(n) < 1) return `$${n.toFixed(3)}`;
  return `$${n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/** 1.2k, 3.4M: token counts at a glance. */
export function formatCount(n: number | null | undefined): string {
  const v = n ?? 0;
  if (Math.abs(v) < 1000) return String(v);
  if (Math.abs(v) < 1_000_000) return `${(v / 1000).toFixed(v < 10_000 ? 1 : 0)}k`;
  return `${(v / 1_000_000).toFixed(1)}M`;
}

/** "Oct 21, 18:04" in the viewer's time zone. */
export function formatStamp(at: string | number | Date | null | undefined): string {
  if (at == null) return '—';
  const d = at instanceof Date ? at : new Date(at);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false });
}

/** A published version's label: the BFF names them `<prefix>-r<N>`; shipped ones keep their name. */
export function versionLabel(harnessVersion: string | null | undefined): string {
  if (!harnessVersion) return '—';
  const m = /-r(\d+)$/.exec(harnessVersion);
  return m ? `v${m[1]}` : harnessVersion;
}
