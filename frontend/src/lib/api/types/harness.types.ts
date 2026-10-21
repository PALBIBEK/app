/**
 * Harness Type Definitions
 *
 * Runtime validation with Zod + TypeScript types
 *
 * References:
 * - FRONTEND_BUILD_CONFIG.md (TypeScript strict mode)
 * - Backend service models analysis
 */

import { z } from 'zod';

// Zod schemas for runtime validation
// A label: the model a session runs is its published manifest's (e.g. `gateway/echo`).
export const HarnessModelSchema = z.string();

export const HarnessBindSchema = z.object({
  bind_status: z.enum(['unbound', 'bound', 'stale', 'error']),
  harness_version: z.string().nullish(),
  runtime: z.string().nullish(),
  composition_id: z.string().nullish(),
  composition_digest: z.string().nullish(),
  bound_at: z.string().nullish(),
  catalog_status: z.enum(['offered', 'deprecated', 'unknown']).nullish(),
});

export const HarnessSchema = z.object({
  id: z.string(),
  name: z.string().min(1, 'Name is required'),
  description: z.string().nullish(),
  model: HarnessModelSchema,
  mode: z.enum(['chat', 'completion', 'agent', 'sticky', 'stateless']),
  tools: z.array(z.string()),
  memory: z.array(z.string()),
  system_prompt: z.string().nullish(),
  temperature: z.number().min(0).max(2).nullish(),
  max_tokens: z.number().positive().nullish(),
  created_at: z.string(),
  updated_at: z.string(),
  created_by: z.string().nullish(),
  updated_by: z.string().nullish(),
  bind: HarnessBindSchema.default({ bind_status: 'unbound' }),
});

export const HarnessCreateSchema = HarnessSchema.omit({
  id: true,
  created_at: true,
  updated_at: true,
  created_by: true,
  updated_by: true,
  bind: true,
});

export const HarnessUpdateSchema = HarnessCreateSchema.partial();

export const HarnessBindRequestSchema = z.object({
  composition_id: z.string().optional(),
  composition_digest: z.string().optional(),
  runtime: z.string().optional(),
});

// TypeScript types inferred from schemas
export type Harness = z.infer<typeof HarnessSchema>;
export type HarnessBind = z.infer<typeof HarnessBindSchema>;
export type HarnessCreate = z.infer<typeof HarnessCreateSchema>;
export type HarnessUpdate = z.infer<typeof HarnessUpdateSchema>;
export type HarnessBindRequest = z.infer<typeof HarnessBindRequestSchema>;

// API response types
export interface HarnessListResponse {
  data: Harness[];
  total: number;
  page: number;
  page_size: number;
}

/** Backend GET/POST/PUT harness returns a flat HarnessResponse, not a wrapped `{ data }` object. */
