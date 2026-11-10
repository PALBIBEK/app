/**
 * Sandbox settings in words, and the starting points for a new environment. Shared by the
 * builder's Sandbox tab and the Environments page.
 */

import type { NetworkSpec, SandboxSpec } from '@/lib/api/types/manifest.types';

export const PROVIDERS: { id: SandboxSpec['provider']; label: string; hint: string }[] = [
  { id: 'local_docker', label: 'Docker', hint: 'A container per session. Isolated; needs Docker where the supervisor runs.' },
  { id: 'e2b', label: 'E2B', hint: 'A hosted micro-VM per session. Isolated; needs an E2B key.' },
  { id: 'local_process', label: 'Host process', hint: 'Runs on the supervisor’s machine. Development only; production refuses it.' },
  { id: 'none', label: 'None', hint: 'No workspace: the agent can only talk.' },
];

export const STARTS: { value: SandboxSpec['start'] | ''; label: string }[] = [
  { value: '', label: 'Platform default (when a tool needs it)' },
  { value: 'with_run', label: 'As each run begins: ready before the first tool' },
  { value: 'on_first_tool', label: 'When a tool needs it: runs without tools never start one' },
];

/** Idle stop choices in seconds ('' is the platform default; 0 never). */
export const IDLE_STOPS: { value: number | ''; label: string }[] = [
  { value: '', label: 'Platform default (15 minutes)' },
  { value: 300, label: 'After 5 minutes' },
  { value: 900, label: 'After 15 minutes' },
  { value: 3600, label: 'After 1 hour' },
  { value: 14400, label: 'After 4 hours' },
  { value: 0, label: 'Never' },
];

/** The tools that drive the session's browser (the supervisor's BROWSER_TOOLS). */
export const BROWSER_TOOLS = ['browser_exec', 'browser_vault', 'browser_dialog', 'browser_cdp'];

/** Providers that enforce a network setting; elsewhere a profile names none. */
export const NETWORKED: SandboxSpec['provider'][] = ['local_docker', 'e2b'];

export const NETWORKS: { value: NetworkSpec['mode'] | ''; label: string }[] = [
  { value: '', label: 'Platform default (nothing, for Docker)' },
  { value: 'none', label: 'Nothing' },
  { value: 'open', label: 'The open web' },
  { value: 'allow', label: 'Only these hosts' },
];

export const BROWSERS: { value: NonNullable<SandboxSpec['browser']>; label: string; hint: string }[] = [
  { value: 'sandbox-chromium', label: 'Chromium in the sandbox', hint: 'Runs next to the code; the sandbox needs the web, or the sites’ hosts.' },
  { value: 'sandbox-lightpanda', label: 'Lightpanda in the sandbox', hint: 'A lightweight engine for text-first browsing; the sandbox’s image must carry it.' },
  { value: 'browserbase', label: 'Browserbase', hint: 'Hosted, with stealth and residential proxies; the sandbox needs only its host (connect.browserbase.com).' },
  { value: 'browser-use', label: 'Browser Use cloud', hint: 'Hosted, with stealth and residential proxies; the sandbox needs only its host.' },
  { value: 'firecrawl', label: 'Firecrawl', hint: 'Hosted browser sessions; the sandbox needs only their host.' },
  { value: 'remote-cdp', label: 'A remote browser (CDP)', hint: 'A hosted browser the platform is set up with; the sandbox needs only its host.' },
];

/** The browsers that run in the sandbox (the rest are hosted: a private address goes to the sandbox’s own). */
export const IN_SANDBOX_BROWSERS: readonly string[] = ['sandbox-chromium', 'sandbox-lightpanda'];

/** Host names typed as a list ("a.com, b.com" or one per line). */
export function parseHosts(text: string): string[] {
  return text.split(/[\s,]+/).map((h) => h.trim().toLowerCase()).filter(Boolean);
}

export function networkLabel(network: NetworkSpec | null | undefined): string | null {
  if (!network) return null;
  if (network.mode === 'none') return 'no network';
  if (network.mode === 'open') return 'the open web';
  return `reaches ${(network.hosts ?? []).join(', ') || 'no host'}`;
}

/** Why browser tools would not reach any site, or null: a Chromium in the sandbox with no network. */
export function browserCannotBrowse(s: SandboxSpec, tools: string[]): string | null {
  const inSandbox = IN_SANDBOX_BROWSERS.includes(s.browser ?? 'sandbox-chromium');
  if (!tools.some((t) => BROWSER_TOOLS.includes(t)) || !inSandbox || !NETWORKED.includes(s.provider)) return null;
  if (s.network?.mode === 'open' || s.network?.mode === 'allow') return null;
  return 'The browser runs in the sandbox, and the sandbox reaches nothing: choose the open web, or the sites’ hosts.';
}

const EFFECTS: Record<SandboxSpec['file_effects'], string> = {
  read_only: 'read only',
  workspace_write: 'writes its workspace',
  danger_full_access: 'full access',
};

export function idleLabel(seconds: number | null | undefined): string {
  if (seconds == null) return 'stops after 15 min idle';
  if (seconds === 0) return 'never stops when idle';
  if (seconds % 3600 === 0) return `stops after ${seconds / 3600} h idle`;
  if (seconds % 60 === 0) return `stops after ${seconds / 60} min idle`;
  return `stops after ${seconds} s idle`;
}

/** One line: "Docker · writes its workspace · starts with each run · stops after 15 min idle". */
export function describeSandbox(s: SandboxSpec): string {
  const provider = PROVIDERS.find((p) => p.id === s.provider)?.label ?? s.provider;
  if (s.provider === 'none') return `${provider} · the agent can only talk`;
  const start = s.start === 'with_run' ? 'starts with each run' : 'starts on first tool';
  const network = NETWORKED.includes(s.provider) ? networkLabel(s.network) : null;
  return [provider, EFFECTS[s.file_effects], network, start, idleLabel(s.idle_stop_s)].filter(Boolean).join(' · ');
}

export const PRESETS: { name: string; description: string; sandbox: SandboxSpec }[] = [
  {
    name: 'Docker, ready on each run',
    description: 'An isolated container, built while the model thinks; stopped after 15 minutes unused.',
    sandbox: { provider: 'local_docker', file_effects: 'workspace_write', approval_default: 'ask', start: 'with_run', idle_stop_s: 900 },
  },
  {
    name: 'Docker with a browser',
    description: 'For browser agents: a container with Chromium that reaches the open web.',
    sandbox: {
      provider: 'local_docker', file_effects: 'workspace_write', approval_default: 'ask', start: 'with_run', idle_stop_s: 900,
      network: { mode: 'open' }, browser: 'sandbox-chromium',
    },
  },
  {
    name: 'Docker, read only',
    description: 'For agents that inspect code but must not change it.',
    sandbox: { provider: 'local_docker', file_effects: 'read_only', approval_default: 'ask', start: 'on_first_tool', idle_stop_s: 300 },
  },
  {
    name: 'Host process (development)',
    description: 'Runs on the supervisor’s machine: fast for local work, refused in production.',
    sandbox: { provider: 'local_process', file_effects: 'danger_full_access', approval_default: 'ask', start: 'with_run', idle_stop_s: 900 },
  },
];
