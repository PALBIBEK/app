/**
 * Starting a session the production way: create answers at once (status `starting`) and the
 * agent worker sets the session up in the background; the page polls its startup status, as
 * clients of OpenAI's background responses or xAI's deferred completions do, until it is
 * `ready` or `failed` (with the reason). A session nobody can start (no worker running) says
 * so instead of leaving the page on "Creating session…".
 */

export interface SessionStartup {
  status: 'starting' | 'ready' | 'failed' | 'closed' | string;
  worker_available: boolean;
  reason?: string | null;
  kind?: string | null;
}

export type StartupOutcome = { ok: true } | { ok: false; reason: string };

export async function waitUntilStarted(
  check: () => Promise<SessionStartup>,
  onProgress: (s: SessionStartup) => void,
  opts: { intervalMs?: number; timeoutMs?: number; shouldStop?: () => boolean; sleep?: (ms: number) => Promise<void> } = {}
): Promise<StartupOutcome> {
  const interval = opts.intervalMs ?? 1000;
  const timeout = opts.timeoutMs ?? 120_000;
  const sleep = opts.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  let waited = 0;
  let last: SessionStartup | null = null;
  while (waited <= timeout) {
    if (opts.shouldStop?.()) return { ok: false, reason: 'stopped' };
    last = await check();
    onProgress(last);
    if (last.status === 'ready') return { ok: true };
    if (last.status === 'failed') return { ok: false, reason: last.reason || 'The session could not start.' };
    if (last.status === 'closed') return { ok: false, reason: 'The session has ended.' };
    await sleep(interval);
    waited += interval;
  }
  return {
    ok: false,
    reason:
      last && !last.worker_available
        ? last.reason || 'No agent worker is running, so the session cannot start.'
        : 'The session is taking too long to start. Check the agent worker (./dev.sh status, ./dev.sh logs worker).',
  };
}

/** What to show while a session starts. */
export function startupMessage(s: SessionStartup | null): string {
  if (!s || s.status === 'starting') {
    if (s && !s.worker_available) return 'Waiting for an agent worker to start this session. None is running right now.';
    return s?.reason || 'Starting the agent…'; // a reason while starting: setup is failing and retried
  }
  return s.status === 'ready' ? 'Ready' : s.reason || s.status;
}
