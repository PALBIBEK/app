/**
 * "Runs on" reads a session's pins as a person would: the agent version, each part's
 * settings in words, and whether anything it runs has been retired since.
 */
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { SessionPins } from '@/lib/api/types/catalog.types';

const pinsQuery = vi.hoisted(() => ({ current: {} as { data?: SessionPins; isLoading: boolean; isError: boolean } }));
vi.mock('@/lib/api/hooks/useSessions', () => ({ useSessionPins: () => pinsQuery.current }));

import { PinnedCard } from './PinnedCard';

const D = 'sha256:' + 'a'.repeat(64);

function pins(over: Partial<SessionPins> = {}): SessionPins {
  return {
    composition_id: 'h-reviewer',
    composition_digest: D,
    composition_semver: '1.2.0',
    composition_status: 'offered',
    release_version: 3,
    harness_version: 'h-reviewer-r3',
    model_route: 'echo',
    mcp_configured: false,
    acp_configured: false,
    parts: [
      { kind: 'prompt_pack', role: 'prompt', artifact_id: 'p', digest: D, semver: '1.0.0', status: 'offered', settings: { persona: 'Reviewer', sections: 2 } },
      { kind: 'tool_set', role: 'tools', artifact_id: 't', digest: D, semver: null, status: 'offered', settings: { tools: [{ name: 'bash' }, { name: 'read_file' }] } },
      {
        kind: 'sandbox_profile',
        role: 'hand:default',
        artifact_id: 's',
        digest: D,
        semver: null,
        status: 'deprecated',
        settings: { provider: 'local_docker', file_effects: 'workspace_only', idle_stop_s: 600, sticky_eligible: true },
      },
      {
        kind: 'memory_profile',
        role: 'memory',
        artifact_id: 'm',
        digest: D,
        semver: null,
        status: 'offered',
        settings: { compaction_enabled: true, context_window: 128000, recall_enabled: true, recall_backend: 'mcp', recall_max_tokens: 512 },
      },
    ],
    ...over,
  };
}

afterEach(cleanup);

describe('PinnedCard', () => {
  it('names the agent version and says each part in words', () => {
    pinsQuery.current = { data: pins(), isLoading: false, isError: false };
    render(<PinnedCard sessionId="s1" />);
    expect(screen.getByText('version 3')).toBeTruthy();
    expect(screen.getByText('Reviewer · 2 sections')).toBeTruthy();
    expect(screen.getByText('bash')).toBeTruthy();
    expect(screen.getByText('Docker · workspace only · stops after 10 min idle')).toBeTruthy();
    expect(screen.getByText(/recall: mcp/)).toBeTruthy();
    expect(screen.getByText('deprecated')).toBeTruthy(); // the retired sandbox part
    expect(screen.getByText('echo')).toBeTruthy();
  });

  it('explains a retired version instead of hiding it', () => {
    pinsQuery.current = { data: pins({ composition_status: 'deprecated' }), isLoading: false, isError: false };
    render(<PinnedCard sessionId="s1" />);
    expect(screen.getByText(/keeps running on it/)).toBeTruthy();
  });

  it('shows nothing for a session on a bare harness', () => {
    pinsQuery.current = { data: undefined, isLoading: false, isError: true };
    const { container } = render(<PinnedCard sessionId="s1" />);
    expect(container.innerHTML).toBe('');
  });
});
