/**
 * Sandbox settings in words, and the starting points for a new environment. Shared by the
 * builder's Sandbox tab and the Environments page.
 */

import type { SandboxSpec } from '@/lib/api/types/manifest.types';

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
  return [provider, EFFECTS[s.file_effects], start, idleLabel(s.idle_stop_s)].join(' · ');
}

export const PRESETS: { name: string; description: string; sandbox: SandboxSpec }[] = [
  {
    name: 'Docker, ready on each run',
    description: 'An isolated container, built while the model thinks; stopped after 15 minutes unused.',
    sandbox: { provider: 'local_docker', file_effects: 'workspace_write', approval_default: 'ask', start: 'with_run', idle_stop_s: 900 },
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
