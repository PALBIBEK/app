/**
 * memoryAPI — the backend's /memory routes only (never the memory service or supervisor).
 */

import { apiClient } from '../client';
import type {
  Fact,
  MemoryHit,
  MemorySearchRequest,
  Notebook,
  NotebookCreateRequest,
  Revision,
} from '../types/memory.types';

const nb = (id: string) => `/memory/notebooks/${encodeURIComponent(id)}`;

export const memoryAPI = {
  /** A harness's notebooks: of its live version, or (allVersions) of every version it had. */
  listNotebooks: (harnessId: string, archived = false, allVersions = false): Promise<Notebook[]> =>
    apiClient.get<Notebook[]>('/memory/notebooks', {
      params: { harness_id: harnessId, archived, ...(allVersions ? { all_versions: true } : {}) },
    }),

  createNotebook: (data: NotebookCreateRequest): Promise<Notebook> =>
    apiClient.post<Notebook>('/memory/notebooks', data),

  setArchived: (id: string, archived: boolean): Promise<Notebook> =>
    apiClient.patch<Notebook>(nb(id), { archived }),

  listFacts: (id: string): Promise<Fact[]> => apiClient.get<Fact[]>(`${nb(id)}/facts`),

  addFacts: (id: string, facts: string[]): Promise<{ added: number }> =>
    apiClient.post<{ added: number }>(`${nb(id)}/facts`, { facts }),

  editFacts: async (id: string, edits: { id: string; text: string }[]): Promise<void> => {
    await apiClient.patch(`${nb(id)}/facts`, { edits });
  },

  forgetFacts: async (id: string, ids: string[]): Promise<void> => {
    await apiClient.post(`${nb(id)}/forget`, { ids });
  },

  clear: (id: string): Promise<{ removed: number }> =>
    apiClient.post<{ removed: number }>(`${nb(id)}/clear`),

  revisions: (id: string): Promise<Revision[]> => apiClient.get<Revision[]>(`${nb(id)}/revisions`),

  restore: async (id: string, revision: number): Promise<void> => {
    await apiClient.post(`${nb(id)}/restore`, { revision });
  },

  search: (body: MemorySearchRequest): Promise<MemoryHit[]> =>
    apiClient.post<MemoryHit[]>('/memory/search', body),
};
