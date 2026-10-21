/**
 * tools.ts — product BFF Tools CRUD (aligned with BE PUT + categories).
 */

import { apiClient } from '../client';
import {
  Tool,
  ToolCreate,
  ToolUpdate,
  ToolListResponse,
  ToolListParams,
  ToolSchema,
} from '../types/tool.types';

const ALLOWED_TOOL_FIELDS = [
  'name',
  'description',
  'category',
  'status',
  'parameters',
  'endpoint',
  'auth_required',
  'rate_limit',
] as const;

function sanitizeToolData<T extends Partial<ToolCreate>>(data: T): Partial<ToolCreate> {
  const sanitized: Partial<ToolCreate> = {};
  for (const field of ALLOWED_TOOL_FIELDS) {
    if (field in data) {
      (sanitized as Record<string, unknown>)[field] = (data as Record<string, unknown>)[field];
    }
  }
  return sanitized;
}

export const toolsAPI = {
  /**
   * List all tools with optional filters
   * GET /tools
   */
  list: async (params?: ToolListParams): Promise<ToolListResponse> => {
    const response = await apiClient.get<ToolListResponse>('/tools', { params });
    return {
      ...response,
      data: (response.data ?? []).map((item) => ToolSchema.parse(item)),
    };
  },

  /**
   * Get tool by ID
   * GET /tools/:id
   */
  getById: async (id: string): Promise<Tool> => {
    const response = await apiClient.get<Tool>(`/tools/${id}`);
    return ToolSchema.parse(response);
  },

  /**
   * Create a new tool
   * POST /tools
   */
  create: async (data: ToolCreate): Promise<Tool> => {
    const sanitizedData = sanitizeToolData(data);
    const response = await apiClient.post<Tool>('/tools', sanitizedData);
    return ToolSchema.parse(response);
  },

  /**
   * Update an existing tool — BE uses PUT (D6 FE/BE alignment).
   * PUT /tools/:id
   */
  update: async (id: string, data: ToolUpdate): Promise<Tool> => {
    const sanitizedData = sanitizeToolData(data);
    const response = await apiClient.put<Tool>(`/tools/${id}`, sanitizedData);
    return ToolSchema.parse(response);
  },

  /**
   * Delete a tool
   * DELETE /tools/:id
   */
  delete: async (id: string): Promise<void> => {
    await apiClient.delete(`/tools/${id}`);
  },
};
