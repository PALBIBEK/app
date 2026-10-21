import { describe, expect, it } from 'vitest';

import type { Manifest } from '@/lib/api/types/manifest.types';

import { diffManifests, errorsBySection, exportHarness, parseImport } from './manifest';

const manifest: Manifest = {
  prompt: { persona: 'coder', sections: [{ id: 'intro', text: 'You are a coding agent in {cwd}.' }] },
  tools: ['read', 'bash'],
  model: 'gateway/echo',
  sandbox: { provider: 'local_docker', file_effects: 'workspace_write', approval_default: 'ask' },
  memory: { compaction: true },
  policy: {
    max_steps: 30,
    max_cost_per_session: 5,
    max_tokens_per_turn: 200000,
    require_approval: ['bash'],
    approval_timeout_s: 300,
    on_approval_timeout: 'deny',
    repetition: 'abort',
    oscillation: 'abort',
    stall_iterations: 10,
  },
};

describe('errorsBySection', () => {
  it('puts a field error on its tab', () => {
    const errors = errorsBySection({
      details: [{ field: 'body.policy.max_steps', message: 'Input should be greater than or equal to 1' }],
    });
    expect(errors.policy).toEqual(['max_steps: Input should be greater than or equal to 1']);
  });

  it('places a cross-field check by the section its message names', () => {
    const errors = errorsBySection({
      details: [{ field: 'body', message: 'Value error, policy.require_approval names tools the harness does not have: git' }],
    });
    expect(errors.policy?.[0]).toMatch(/^policy.require_approval names tools/);
  });

  it('puts a publish refusal on the section the backend names', () => {
    expect(errorsBySection({ detail: { section: 'model', message: "'x' is not served" } })).toEqual({
      model: ["'x' is not served"],
    });
  });

  it('keeps anything else as a general error', () => {
    expect(errorsBySection({ detail: 'Harness not found' })).toEqual({ general: ['Harness not found'] });
  });
});

describe('diffManifests', () => {
  it('lists changed leaves by path, arrays whole', () => {
    const after: Manifest = { ...manifest, tools: ['read'], policy: { ...manifest.policy, max_steps: 12 } };
    expect(diffManifests(manifest, after).map((c) => c.path)).toEqual(['tools', 'policy.max_steps']);
  });

  it('finds nothing in an unchanged manifest', () => {
    expect(diffManifests(manifest, structuredClone(manifest))).toEqual([]);
  });
});

describe('export and import', () => {
  it('round-trips a harness', () => {
    expect(parseImport(exportHarness('coder', null, manifest))).toEqual(manifest);
  });

  it('accepts a bare manifest', () => {
    expect(parseImport(JSON.stringify(manifest))).toEqual(manifest);
  });

  it('says what is wrong with a file that is not a manifest', () => {
    expect(() => parseImport('{"prompt": {}}')).toThrow(/Not a harness manifest: prompt/);
    expect(() => parseImport('nope')).toThrow('Not a JSON file');
  });
});
