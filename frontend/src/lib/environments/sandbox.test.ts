import { describe, expect, it } from 'vitest';

import { browserCannotBrowse, describeSandbox, idleLabel, parseHosts, PRESETS } from './sandbox';

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

  it('says what the sandbox may reach, where a provider enforces it', () => {
    const browser = PRESETS.find((p) => p.name === 'Docker with a browser')!.sandbox;
    expect(describeSandbox(browser)).toBe('Docker · writes its workspace · the open web · starts with each run · stops after 15 min idle');
    const allow = { ...browser, network: { mode: 'allow' as const, hosts: ['cdp.example', 'api.example'] } };
    expect(describeSandbox(allow)).toContain('reaches cdp.example, api.example');
    expect(describeSandbox({ ...browser, provider: 'local_process' })).not.toContain('open web');
  });
});

describe('browser settings', () => {
  const docker = PRESETS[0].sandbox;

  it('reads hosts typed as a list', () => {
    expect(parseHosts(' CDP.example.com, api.example.com\nb.example ')).toEqual(['cdp.example.com', 'api.example.com', 'b.example']);
    expect(parseHosts('a.com, ')).toEqual(['a.com']);
  });

  it('warns when a browser in the sandbox could reach no site', () => {
    expect(browserCannotBrowse(docker, ['browser_exec'])).toMatch(/reaches nothing/);
    expect(browserCannotBrowse({ ...docker, network: { mode: 'open' } }, ['browser_exec'])).toBeNull();
    expect(browserCannotBrowse({ ...docker, browser: 'remote-cdp' }, ['browser_exec'])).toBeNull();
    expect(browserCannotBrowse({ ...docker, browser: 'browserbase' }, ['browser_exec'])).toBeNull();
    expect(browserCannotBrowse({ ...docker, browser: 'sandbox-lightpanda' }, ['browser_exec'])).not.toBeNull();
    expect(browserCannotBrowse(docker, ['bash'])).toBeNull();
  });
});
