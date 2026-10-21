/**
 * Long-term memory: a user's notebooks for one harness, and their facts.
 * Mirrors the backend's /memory routes (which proxy the supervisor's notebooks).
 */

export interface Notebook {
  id: string;
  agent_pin: string;
  /** The harness release this notebook's agent version is, when known. */
  version?: number | null;
  name: string;
  label: string | null;
  revision: number;
  created_at: number;
  archived: boolean;
  facts: number;
  bytes: number;
  updated_at: number | null;
}

export interface Fact {
  id: string;
  text: string;
  sensitivity: string;
  sources: string[];
  source_unknown: boolean;
  author: string | null;
  created_at: number;
  updated_at: number;
}

export interface Revision {
  revision: number;
  fact_id: string;
  op: 'insert' | 'update' | 'delete' | string;
  text: string;
  sensitivity: string;
  at: number;
  author: string | null;
}

export interface NotebookCreateRequest {
  harness_id: string;
  name: string;
  label?: string | null;
}

export interface MemorySearchRequest {
  harness_id: string;
  query: string;
  notebook_id?: string;
  limit?: number;
}

export interface MemoryHit {
  notebook_id: string;
  fact: string;
}
