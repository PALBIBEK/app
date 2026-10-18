/**
 * Query keys derive from the URL / route identity (design.md §3 / §4).
 * Phase 1 stub — features will pass validated route search objects later.
 */
export function queryKeyFromRoute(
  route: string,
  search?: Record<string, unknown>,
): readonly unknown[] {
  if (search === undefined) {
    return ["route", route] as const;
  }
  return ["route", route, search] as const;
}
