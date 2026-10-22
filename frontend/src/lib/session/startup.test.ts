import { describe, expect, it, vi } from 'vitest';

import { startupMessage, waitUntilStarted, type SessionStartup } from './startup';

const noSleep = async () => {};

describe('waitUntilStarted', () => {
  it('polls until the session is ready', async () => {
    const answers: SessionStartup[] = [
      { status: 'starting', worker_available: true },
      { status: 'starting', worker_available: true },
      { status: 'ready', worker_available: true },
    ];
    const seen = vi.fn();
    const out = await waitUntilStarted(async () => answers.shift()!, seen, { sleep: noSleep });
    expect(out).toEqual({ ok: true });
    expect(seen).toHaveBeenCalledTimes(3);
  });

  it('stops with the reason when the start fails', async () => {
    const out = await waitUntilStarted(
      async () => ({ status: 'failed', worker_available: true, reason: 'Docker is not reachable' }),
      () => {},
      { sleep: noSleep }
    );
    expect(out).toEqual({ ok: false, reason: 'Docker is not reachable' });
  });

  it('gives up after the timeout, saying when no worker is running', async () => {
    const out = await waitUntilStarted(
      async () => ({ status: 'starting', worker_available: false, reason: 'No agent worker is running, start it' }),
      () => {},
      { sleep: noSleep, intervalMs: 10, timeoutMs: 30 }
    );
    expect(out).toEqual({ ok: false, reason: 'No agent worker is running, start it' });
  });
});

describe('waitUntilStarted errors', () => {
  it('keeps watching through a few failed checks, then gives up', async () => {
    let n = 0;
    const flaky = async (): Promise<SessionStartup> => {
      n += 1;
      if (n <= 2) throw new Error('503');
      return { status: 'ready', worker_available: true };
    };
    expect(await waitUntilStarted(flaky, () => {}, { sleep: noSleep })).toEqual({ ok: true });
    await expect(
      waitUntilStarted(async () => { throw new Error('down'); }, () => {}, { sleep: noSleep })
    ).rejects.toThrow('down');
  });
});

describe('startupMessage', () => {
  it('says what is happening', () => {
    expect(startupMessage(null)).toBe('Starting the agent…');
    expect(startupMessage({ status: 'starting', worker_available: false })).toMatch(/None is running/);
    expect(startupMessage({ status: 'starting', worker_available: true, reason: 'retrying: boom' })).toBe('retrying: boom');
  });
});
