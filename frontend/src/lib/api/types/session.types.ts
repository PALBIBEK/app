/**
 * Session types — product BFF (D4), aligned with AI status vocabulary.
 */

import { z } from 'zod';

export const SessionStatusSchema = z.enum([
  'active',
  'paused',
  'terminated',
  'failed',
  'awaiting_approval',
  'budget_exceeded',
]);

export const SessionPinSchema = z.object({
  harness_version: z.string(),
  runtime: z.string(),
  composition_id: z.string().nullish(),
  composition_digest: z.string().nullish(),
});

export const SessionSchema = z.object({
  id: z.string(),
  harness_id: z.string(),
  status: z.string(),
  harness_version: z.string(),
  runtime: z.string(),
  composition_id: z.string().nullish(),
  composition_digest: z.string().nullish(),
  title: z.string().nullish(),
  message_count: z.number(),
  total_cost: z.number(),
  last_seq: z.number(),
  is_busy: z.boolean().nullish(),
  created_at: z.string(),
  updated_at: z.string(),
  pin: SessionPinSchema,
});

export const SessionListResponseSchema = z.object({
  data: z.array(SessionSchema),
  total: z.number(),
  page: z.number(),
  page_size: z.number(),
});

export const SessionMessageResponseSchema = z.object({
  turn_id: z.string(),
  command_id: z.string(),
  status: z.string(),
});

export const WireEventSchema = z.object({
  seq: z.number(),
  event_id: z.string(),
  turn_id: z.string().nullish(),
  type: z.string(),
  ts_ms: z.number(),
  data: z.record(z.unknown()).default({}),
});

export type Session = z.infer<typeof SessionSchema>;
export type SessionListResponse = z.infer<typeof SessionListResponseSchema>;
export type SessionMessageResponse = z.infer<typeof SessionMessageResponseSchema>;
export type WireEvent = z.infer<typeof WireEventSchema>;

export interface SessionCreateRequest {
  harness_id: string;
  scope_id?: string;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: string;
  toolCalls?: ToolCallView[];
}

export interface ToolCallView {
  id: string;
  name: string;
  args?: Record<string, unknown>;
  output?: unknown;
  status: 'running' | 'success' | 'error';
  error?: string;
}
