/**
 * memoryAPI — product BFF /memory only (never Durable Memory host).
 */

import { apiClient } from '../client';
import type {
  MemoryCreateRequest,
  MemoryEntry,
  MemoryListParams,
  MemoryListResponse,
  MemorySearchRequest,
  MemorySearchResponse,
  MemoryUpdateRequest,
} from '../types/memory.types';

export const memoryAPI = {
  list: async (params?: MemoryListParams): Promise<MemoryListResponse> => {
    return apiClient.get<MemoryListResponse>('/memory', { params });
  },

  getById: async (id: string): Promise<MemoryEntry> => {
    const response = await apiClient.get<{ data: MemoryEntry } | MemoryEntry>(`/memory/${id}`);
    if (response && typeof response === 'object' && 'data' in response && response.data) {
      return response.data;
    }
    return response as MemoryEntry;
  },

  create: async (data: MemoryCreateRequest): Promise<MemoryEntry> => {
    const response = await apiClient.post<{ data: MemoryEntry } | MemoryEntry>('/memory', data);
    if (response && typeof response === 'object' && 'data' in response && response.data) {
      return response.data;
    }
    return response as MemoryEntry;
  },

  update: async (id: string, data: MemoryUpdateRequest): Promise<MemoryEntry> => {
    const response = await apiClient.put<{ data: MemoryEntry } | MemoryEntry>(
      `/memory/${id}`,
      data
    );
    if (response && typeof response === 'object' && 'data' in response && response.data) {
      return response.data;
    }
    return response as MemoryEntry;
  },

  delete: async (id: string): Promise<void> => {
    await apiClient.delete(`/memory/${id}`);
  },

  search: async (body: MemorySearchRequest): Promise<MemorySearchResponse> => {
    return apiClient.post<MemorySearchResponse>('/memory/search', body);
  },
};
