import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';

import { apiClient } from '../client';
import { harnessesAPI } from './harnesses';
import type { Harness } from '../types/harness.types';

vi.mock('../client', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}));

const createMockHarness = (overrides: Partial<Harness> = {}): Harness => ({
  id: 'harness-123',
  name: 'coding-assistant-v1',
  description: 'A coding assistant harness for Python development',
  model: 'claude-sonnet-4',
  mode: 'agent',
  tools: ['code-analyzer', 'file-writer'],
  memory: ['project-context'],
  system_prompt: 'You are a helpful coding assistant.',
  temperature: 0.7,
  max_tokens: 4096,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
  bind: { bind_status: 'unbound' },
  ...overrides,
});

describe('harnessesAPI', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('gets a harness by id', async () => {
    const mockHarness = createMockHarness();
    (apiClient.get as Mock).mockResolvedValue(mockHarness);
    const result = await harnessesAPI.getById('harness-123');
    expect(apiClient.get).toHaveBeenCalledWith('/harnesses/harness-123');
    expect(result).toEqual(mockHarness);
  });

  it('creates a harness', async () => {
    const mockResponse = createMockHarness({ name: 'new-harness' });
    (apiClient.post as Mock).mockResolvedValue(mockResponse);
    const newHarness = {
      name: 'new-harness',
      description: 'A new harness',
      model: 'claude-sonnet-4' as const,
      mode: 'agent' as const,
      tools: [],
      memory: [],
    };
    const result = await harnessesAPI.create(newHarness as never);
    expect(apiClient.post).toHaveBeenCalled();
    expect(result).toEqual(mockResponse);
  });

  it('updates a harness via put', async () => {
    const mockResponse = createMockHarness({ description: 'updated' });
    (apiClient.put as Mock).mockResolvedValue(mockResponse);
    const updateData = { description: 'updated' };
    const result = await harnessesAPI.update('harness-123', updateData as never);
    expect(apiClient.put).toHaveBeenCalledWith('/harnesses/harness-123', updateData);
    expect(result).toEqual(mockResponse);
  });

  describe('Edge Cases', () => {
    it('rejects invalid get payloads by returning the upstream value', async () => {
      const invalidHarness = { invalid: true };
      (apiClient.get as Mock).mockResolvedValue(invalidHarness);
      await expect(harnessesAPI.getById('harness-123')).rejects.toThrow();
    });
  });
});
