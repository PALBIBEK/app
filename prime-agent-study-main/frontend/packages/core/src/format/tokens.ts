export type TokensOptions = {
  /** BCP 47 locale — never ambient-only. */
  locale: string;
};

/**
 * Grouped token count for tabular display (e.g. `1,420`).
 */
export function formatTokens(n: number, options: TokensOptions): string {
  const { locale } = options;
  return new Intl.NumberFormat(locale, {
    maximumFractionDigits: 0,
  }).format(n);
}
