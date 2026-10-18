/**
 * Tool Type Definitions — aligned with BFF ToolService categories (D6).
 */

import { z } from 'zod';

export const TOOL_CATEGORIES = [
  'search',
  'data',
  'code',
  'communication',
  'system',
  'ai',
  'custom',
] as const;

export type ToolCategory = (typeof TOOL_CATEGORIES)[number];

export const ToolParameterSchema = z.object({
  name: z.string(),
  type: z.enum(['string', 'number', 'boolean', 'array', 'object']),
  description: z.string(),
  required: z.boolean().default(false),
  default: z.any().optional(),
});

export const ToolSchema = z.object({
  id: z.string(),
  name: z.string().min(1, 'Name is required'),
  description: z.string(),
  category: z.enum(TOOL_CATEGORIES),
  status: z.enum(['active', 'inactive', 'deprecated', 'beta']),
  parameters: z.array(ToolParameterSchema),
  endpoint: z.string().url().nullish(),
  auth_required: z.boolean().default(false),
  rate_limit: z.number().positive().nullish(),
  created_at: z.string(),
  updated_at: z.string(),
  created_by: z.string().nullish(),
  updated_by: z.string().nullish(),
});

export const ToolCreateSchema = ToolSchema.omit({
  id: true,
  created_at: true,
  updated_at: true,
  created_by: true,
  updated_by: true,
});

export const ToolUpdateSchema = ToolCreateSchema.partial();

export const ToolExecutionResultSchema = z.object({
  tool_id: z.string(),
  execution_id: z.string(),
  status: z.enum(['success', 'error', 'timeout']),
  result: z.any().optional(),
  error: z.string().optional(),
  duration_ms: z.number().nonnegative(),
  timestamp: z.string().datetime(),
});

export type ToolParameter = z.infer<typeof ToolParameterSchema>;
export type Tool = z.infer<typeof ToolSchema>;
export type ToolCreate = z.infer<typeof ToolCreateSchema>;
export type ToolUpdate = z.infer<typeof ToolUpdateSchema>;
export type ToolExecutionResult = z.infer<typeof ToolExecutionResultSchema>;

export interface ToolListResponse {
  data: Tool[];
  total: number;
  page: number;
  page_size: number;
}

export interface ToolDetailResponse {
  data: Tool;
}

export interface ToolExecuteRequest {
  tool_id: string;
  parameters: Record<string, unknown>;
}

export interface ToolExecuteResponse {
  execution_id: string;
  result: ToolExecutionResult;
}

export interface ToolListParams {
  category?: ToolCategory;
  status?: Tool['status'];
  search?: string;
  page?: number;
  page_size?: number;
}
