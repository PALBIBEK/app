export type DurationOptions = {
  /** BCP 47 locale reserved for future localized unit labels. */
  locale?: string;
};

/**
 * Compact duration: `1m 24s`, `2h 3m`, `500ms`.
 */
export function formatDuration(ms: number, options: DurationOptions = {}): string {
  void options;
  if (!Number.isFinite(ms)) return "0s";
  const sign = ms < 0 ? "-" : "";
  let remaining = Math.abs(Math.round(ms));

  if (remaining < 1000) {
    return `${sign}${remaining}ms`;
  }

  const hours = Math.floor(remaining / 3_600_000);
  remaining -= hours * 3_600_000;
  const minutes = Math.floor(remaining / 60_000);
  remaining -= minutes * 60_000;
  const seconds = Math.floor(remaining / 1000);

  const parts: string[] = [];
  if (hours > 0) parts.push(`${hours}h`);
  if (minutes > 0) parts.push(`${minutes}m`);
  if (seconds > 0 || parts.length === 0) parts.push(`${seconds}s`);

  return `${sign}${parts.join(" ")}`;
}
