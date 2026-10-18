/**
 * tools.test.ts — toolsAPI unit tests (BFF PUT + unwrapped Tool body).
 */

import { describe, it, expect, beforeEach, vi, Mock } from 'vitest';
import { apiClient } from '../client';
import { toolsAPI } from './tools';
import type { Tool, ToolListResponse, ToolExecutionResult } from '../types/tool.types';

vi.mock('../client', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

const createMockTool = (overrides: Partial<Tool> = {}): Tool => ({
  id: 'tool-123',
  name: 'Web Search',
  description: 'Search the web for information',
  category: 'search',
  status: 'active',
  parameters: [
    {
      name: 'query',
      type: 'string',
      description: 'Search query',
      required: true,
    },
  ],
  auth_required: false,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
  ...overrides,
});

const createMockToolListResponse = (): ToolListResponse => ({
  data: [createMockTool()],
  total: 1,
  page: 1,
  page_size: 20,
});

describe('toolsAPI', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('list', () => {
    it('should fetch tools list without parameters', async () => {
      const mockResponse = createMockToolListResponse();
      (apiClient.get as Mock).mockResolvedValue(mockResponse);

      const result = await toolsAPI.list();

      expect(apiClient.get).toHaveBeenCalledWith('/tools', { params: undefined });
      expect(result.data).toHaveLength(1);
      expect(result.total).toBe(1);
    });

    it('should fetch tools with category filter', async () => {
      const mockResponse = createMockToolListResponse();
      (apiClient.get as Mock).mockResolvedValue(mockResponse);
      const params = { category: 'search' as const };

      const result = await toolsAPI.list(params);

      expect(apiClient.get).toHaveBeenCalledWith('/tools', { params });
      expect(result.data[0].category).toBe('search');
    });

    it('should fetch tools with status filter', async () => {
      const mockResponse = createMockToolListResponse();
      (apiClient.get as Mock).mockResolvedValue(mockResponse);
      const params = { status: 'active' as const };

      await toolsAPI.list(params);

      expect(apiClient.get).toHaveBeenCalledWith('/tools', { params });
    });

    it('should fetch tools with search parameter', async () => {
      const mockResponse = createMockToolListResponse();
      (apiClient.get as Mock).mockResolvedValue(mockResponse);
      const params = { search: 'web' };

      await toolsAPI.list(params);

      expect(apiClient.get).toHaveBeenCalledWith('/tools', { params });
    });
  });

  describe('getById', () => {
    it('should fetch tool by ID', async () => {
      const mockTool = createMockTool();
      (apiClient.get as Mock).mockResolvedValue(mockTool);

      const result = await toolsAPI.getById('tool-123');

      expect(apiClient.get).toHaveBeenCalledWith('/tools/tool-123');
      expect(result.id).toBe('tool-123');
      expect(result.name).toBe('Web Search');
    });

    it('should validate response with Zod schema', async () => {
      const invalidTool = {
        id: 'tool-123',
      };
      (apiClient.get as Mock).mockResolvedValue(invalidTool);

      await expect(toolsAPI.getById('tool-123')).rejects.toThrow();
    });
  });

  describe('create', () => {
    it('should create a new tool', async () => {
      const newToolData = {
        name: 'Code Runner',
        description: 'Execute code snippets',
        category: 'code' as const,
        status: 'active' as const,
        parameters: [],
        auth_required: false,
      };
      const mockCreatedTool = createMockTool({
        id: 'tool-new',
        name: 'Code Runner',
        description: 'Execute code snippets',
        category: 'code',
      });
      (apiClient.post as Mock).mockResolvedValue(mockCreatedTool);

      const result = await toolsAPI.create(newToolData);

      expect(apiClient.post).toHaveBeenCalledWith('/tools', newToolData);
      expect(result.name).toBe('Code Runner');
      expect(result.category).toBe('code');
    });
  });

  describe('update', () => {
    it('should update an existing tool via PUT', async () => {
      const updateData = { description: 'Updated description' };
      const mockUpdatedTool = createMockTool({ description: 'Updated description' });
      (apiClient.put as Mock).mockResolvedValue(mockUpdatedTool);

      const result = await toolsAPI.update('tool-123', updateData);

      expect(apiClient.put).toHaveBeenCalledWith('/tools/tool-123', updateData);
      expect(result.description).toBe('Updated description');
    });

    it('should update tool status', async () => {
      const updateData = { status: 'inactive' as const };
      const mockUpdatedTool = createMockTool({ status: 'inactive' });
      (apiClient.put as Mock).mockResolvedValue(mockUpdatedTool);

      const result = await toolsAPI.update('tool-123', updateData);

      expect(result.status).toBe('inactive');
    });
  });

  describe('delete', () => {
    it('should delete a tool', async () => {
      (apiClient.delete as Mock).mockResolvedValue(undefined);

      await toolsAPI.delete('tool-123');

      expect(apiClient.delete).toHaveBeenCalledWith('/tools/tool-123');
    });
  });

  describe('execute', () => {
    it('should execute a tool', async () => {
      const executeRequest = {
        tool_id: 'tool-123',
        parameters: { query: 'test' },
      };
      const mockResult: ToolExecutionResult = {
        tool_id: 'tool-123',
        execution_id: 'exec-1',
        status: 'success',
        result: { hits: [] },
        duration_ms: 12,
        timestamp: '2024-01-01T00:00:00Z',
      };
      (apiClient.post as Mock).mockResolvedValue({
        execution_id: 'exec-1',
        result: mockResult,
      });

      const result = await toolsAPI.execute(executeRequest);

      expect(apiClient.post).toHaveBeenCalledWith('/tools/tool-123/execute', {
        parameters: { query: 'test' },
      });
      expect(result.result.status).toBe('success');
    });
  });
});
