import { describe, expect, it } from 'vitest';

import { describeSandbox, idleLabel, PRESETS } from './sandbox';

describe('sandbox in words', () => {
  it('names the idle stop', () => {
    expect(idleLabel(undefined)).toBe('stops after 15 min idle');
    expect(idleLabel(0)).toBe('never stops when idle');
    expect(idleLabel(3600)).toBe('stops after 1 h idle');
    expect(idleLabel(300)).toBe('stops after 5 min idle');
    expect(idleLabel(45)).toBe('stops after 45 s idle');
  });

  it('describes a sandbox in one line', () => {
    expect(describeSandbox(PRESETS[0].sandbox)).toBe('Docker · writes its workspace · starts with each run · stops after 15 min idle');
    expect(describeSandbox({ provider: 'none', file_effects: 'read_only', approval_default: 'ask' })).toBe('None · the agent can only talk');
  });
});
