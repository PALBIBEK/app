/** The dashboard's numbers, from the harness list and the newest page of sessions. */

export interface DashboardStats {
  harnesses: number;
  live: number;
  sessions: number;
  /** Sessions started since local midnight; `todayCapped` when the page held only today's. */
  today: number;
  todayCapped: boolean;
}

export function dashboardStats(
  harnesses: { bind?: { bind_status?: string } }[] | undefined,
  sessions: { total: number; data: { created_at: string }[] } | undefined,
  now: Date = new Date(),
): DashboardStats {
  const list = harnesses ?? [];
  const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const page = sessions?.data ?? [];
  const today = page.filter((s) => Date.parse(s.created_at) >= midnight).length;
  return {
    harnesses: list.length,
    live: list.filter((h) => h.bind?.bind_status === 'bound').length,
    sessions: sessions?.total ?? 0,
    today,
    // The page is newest first: if every one is from today, there may be more.
    todayCapped: page.length > 0 && today === page.length && (sessions?.total ?? 0) > page.length,
  };
}
