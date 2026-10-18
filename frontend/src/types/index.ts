export type SessionStatus = 'running' | 'completed' | 'failed' | 'paused' | 'waiting_approval';

export interface Session {
  id: string;
  harness_id: string;
  status: SessionStatus;
  title?: string | null;
  scope_id?: string | null;
  created_at: string;
  updated_at: string;
}

export type { Tool, ToolCreate, ToolUpdate } from '@/lib/api/types/tool.types';
export type { Harness } from '@/lib/api/types/harness.types';
