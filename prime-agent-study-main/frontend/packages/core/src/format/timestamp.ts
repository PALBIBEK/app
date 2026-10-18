export type TimestampOptions = {
  /** BCP 47 locale — never rely on ambient defaults alone. */
  locale: string;
  /** IANA timezone — required so operators never misread UTC as local. */
  timeZone: string;
  dateStyle?: "full" | "long" | "medium" | "short";
  timeStyle?: "full" | "long" | "medium" | "short";
};

/**
 * Format an epoch-ms timestamp with explicit locale and timezone.
 */
export function formatTimestamp(ms: number, options: TimestampOptions): string {
  const { locale, timeZone, dateStyle = "medium", timeStyle = "medium" } = options;
  return new Intl.DateTimeFormat(locale, {
    dateStyle,
    timeStyle,
    timeZone,
  }).format(new Date(ms));
}
