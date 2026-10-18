export type CostOptions = {
  /** BCP 47 locale — never ambient-only. */
  locale: string;
  /** ISO 4217 currency code — never implied. */
  currency: string;
};

/**
 * Format a monetary amount with an explicit currency and locale.
 */
export function formatCost(amount: number, options: CostOptions): string {
  const { locale, currency } = options;
  return new Intl.NumberFormat(locale, {
    style: "currency",
    currency,
  }).format(amount);
}
