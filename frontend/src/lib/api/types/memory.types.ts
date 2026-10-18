/**
 * Memory BFF types — outer /memory projection of Durable Memory (D7).
 */

export type MemoryType =
  | 'short_term'
  | 'long_term'
  | 'episodic'
  | 'semantic'
  | 'procedural';

export type MemorySource = 'user' | 'agent' | 'system' | 'external';

export interface MemoryEntry {
  id: string;
  content: string;
  type: MemoryType;
  source: MemorySource;
  session_id?: string | null;
  harness_id?: string | null;
  metadata?: Record<string, unknown>;
  relevance_score?: number | null;
  created_at: string;
  expires_at?: string | null;
}

/** Alias used by some pages / stubs */
export type MemoryItem = MemoryEntry;

export interface MemoryListResponse {
  data: MemoryEntry[];
  total: number;
  page: number;
  page_size: number;
}

export interface MemoryDetailResponse {
  data: MemoryEntry;
}

export interface MemoryCreateRequest {
  content: string;
  type?: MemoryType;
  source?: MemorySource;
  session_id?: string | null;
  harness_id?: string | null;
  metadata?: Record<string, unknown>;
  namespace?: string | null;
  expires_at?: string | null;
}

export interface MemoryUpdateRequest {
  content?: string;
  metadata?: Record<string, unknown> | null;
  expires_at?: string | null;
  namespace?: string | null;
}

export interface MemorySearchRequest {
  query: string;
  type?: MemoryType;
  session_id?: string;
  harness_id?: string;
  limit?: number;
  min_relevance?: number;
  namespace?: string;
}

export interface MemorySearchResponse {
  results: MemoryEntry[];
  query: string;
  total_found: number;
}

export interface MemoryListParams {
  page?: number;
  page_size?: number;
  namespace?: string;
}
