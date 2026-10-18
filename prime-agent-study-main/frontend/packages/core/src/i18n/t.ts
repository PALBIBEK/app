import { catalog, type CatalogKey } from "./catalog.en";

/**
 * Look up a catalog string and interpolate `{var}` placeholders.
 */
export function t(
  key: CatalogKey,
  vars?: Record<string, string | number | boolean>,
): string {
  const template = catalog[key];
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (match, name: string) => {
    const value = vars[name];
    return value === undefined ? match : String(value);
  });
}
