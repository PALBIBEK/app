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
  scope_id: z.string().nullish(),
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
  /** True when the message waits behind a turn already running. */
  queued: z.boolean().default(false),
  /** True when no worker was up: the message is kept and runs once one comes. */
  held: z.boolean().default(false),
});

/** One run: what happened to one message (fetched by the turn_id its message got). */
export const RunSchema = z.object({
  run_id: z.string(),
  command_id: z.string(),
  status: z.enum(['queued', 'in_progress', 'requires_action', 'completed', 'failed', 'cancelled', 'incomplete']),
  created_at_ms: z.number(),
  started_at_ms: z.number().nullable().default(null),
  ended_at_ms: z.number().nullable().default(null),
  stop_reason: z.string().nullable().default(null),
  output_text: z.string().nullable().default(null),
  error: z.record(z.string()).nullable().default(null),
  usage: z
    .object({
      input_tokens: z.number().default(0),
      output_tokens: z.number().default(0),
      total_tokens: z.number().default(0),
      cost: z.number().default(0),
    })
    .partial()
    .default({}),
  steps: z.number().default(0),
  tool_calls: z.number().default(0),
  pending_approval_id: z.string().nullable().default(null),
});

export const WireEventSchema = z.object({
  seq: z.number(),
  event_id: z.string(),
  turn_id: z.string().nullish(),
  type: z.string(),
  ts_ms: z.number(),
  data: z.record(z.unknown()).default({}),
});

/** A subagent below a session: a session its agent started with a tool call. */
export const SubagentSchema = z.object({
  id: z.string(),
  parent_id: z.string(),
  depth: z.number(),
  label: z.string().nullish(),
  task: z.string().nullish(),
  /** foreground: the agent waited for it. background: it worked while the agent went on.
   * continuable: it stays, and takes more messages. */
  kind: z.enum(['foreground', 'background', 'continuable']),
  mode: z.enum(['spawn', 'fork']),
  /** ready: a continuable subagent waiting for its next message. */
  status: z.enum(['starting', 'running', 'ready', 'completed', 'failed', 'cancelled']),
  outcome: z.enum(['completed', 'failed', 'cancelled']).nullish(),
  answer: z.string().nullish(),
  answer_truncated: z.boolean().default(false),
  open: z.boolean(),
  call_id: z.string().nullish(),
  cost_usd: z.number().default(0),
  created_at: z.string(),
});

export const SubagentListSchema = z.object({ subagents: z.array(SubagentSchema) });

export type Subagent = z.infer<typeof SubagentSchema>;
export type Session = z.infer<typeof SessionSchema>;
export type SessionListResponse = z.infer<typeof SessionListResponseSchema>;
export type SessionMessageResponse = z.infer<typeof SessionMessageResponseSchema>;
export type WireEvent = z.infer<typeof WireEventSchema>;
export type Run = z.infer<typeof RunSchema>;

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
  /** The journal position, for "fork from here" (absent on a message not yet confirmed). */
  seq?: number;
  /** Set when a subagent told the agent something (it finished, or sent an update). */
  notice?: SubagentNoticeView;
}

/** What a subagent told the agent that started it, as a person reads it. */
export interface SubagentNoticeView {
  subagentId: string;
  /** settled: a turn of its ended. report: it sent an update while working. */
  kind: 'settled' | 'report';
  /** How a settled turn ended. */
  status?: 'completed' | 'failed' | 'cancelled';
  /** Its own words: its closing message, or the update. */
  text: string;
}

export interface ToolCallView {
  id: string;
  name: string;
  args?: Record<string, unknown>;
  output?: unknown;
  status: 'running' | 'success' | 'error';
  error?: string;
  /** The supervisor's refusal or failure code (STEP_LIMIT, APPROVAL_DENIED, …). */
  code?: string;
  /** What happened, in plain words, when the call failed. */
  explanation?: string;
}
