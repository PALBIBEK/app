/**
 * Agent workspace files API (D9) — durable SoT via BFF, never supervisor host.
 * Distinct from tenant Settings "workspace" org chrome.
 */

import { apiClient } from '../client';

export interface WorkspaceFile {
  path: string;
  content_sha256: string;
  object_key: string;
  size_bytes: number;
  version: number;
  mime_type?: string | null;
  file_id: string;
}

export interface ScopeMember {
  principal_id: string;
  grants: string[];
  email?: string | null;
  name?: string | null;
}

const filePath = (path: string) => path.split('/').map(encodeURIComponent).join('/');

export interface EnsureScopeResponse {
  scope_id: string;
  status: string;
}

export const workspaceAPI = {
  ensureScope: async (scopeId: string): Promise<EnsureScopeResponse> => {
    return apiClient.post<EnsureScopeResponse>(
      `/workspace/scopes/${encodeURIComponent(scopeId)}/ensure`
    );
  },

  listFiles: async (scopeId: string): Promise<WorkspaceFile[]> => {
    return apiClient.get<WorkspaceFile[]>(
      `/workspace/scopes/${encodeURIComponent(scopeId)}/files`
    );
  },

  uploadFile: async (
    scopeId: string,
    path: string,
    file: File | Blob,
    mimeType?: string
  ): Promise<WorkspaceFile> => {
    const form = new FormData();
    form.append('upload', file, path.split('/').pop() || 'upload.bin');
    const params = new URLSearchParams();
    if (mimeType) params.set('mime_type', mimeType);
    const q = params.toString() ? `?${params}` : '';
    // Let the browser set multipart boundary (do not force Content-Type).
    return apiClient.put<WorkspaceFile>(
      `/workspace/scopes/${encodeURIComponent(scopeId)}/files/${path
        .split('/')
        .map(encodeURIComponent)
        .join('/')}${q}`,
      form,
      { headers: { 'Content-Type': undefined } }
    );
  },

  deleteFile: async (scopeId: string, path: string): Promise<void> => {
    await apiClient.delete(
      `/workspace/scopes/${encodeURIComponent(scopeId)}/files/${path
        .split('/')
        .map(encodeURIComponent)
        .join('/')}`
    );
  },

  /** The file's bytes, for viewing or saving. */
  getFile: async (scopeId: string, path: string): Promise<Blob> =>
    apiClient.get<Blob>(`/workspace/scopes/${encodeURIComponent(scopeId)}/files/${filePath(path)}`, {
      responseType: 'blob',
    }),

  listMembers: async (scopeId: string): Promise<ScopeMember[]> =>
    apiClient.get<ScopeMember[]>(`/workspace/scopes/${encodeURIComponent(scopeId)}/members`),

  /** Share the scope with another user, by email. */
  inviteMember: async (scopeId: string, email: string): Promise<ScopeMember> =>
    apiClient.post<ScopeMember>(`/workspace/scopes/${encodeURIComponent(scopeId)}/members`, { email }),

  removeMember: async (scopeId: string, principalId: string): Promise<void> => {
    await apiClient.delete(
      `/workspace/scopes/${encodeURIComponent(scopeId)}/members/${encodeURIComponent(principalId)}`
    );
  },

  rehydrateSession: async (sessionId: string): Promise<{ status: string }> => {
    return apiClient.post<{ status: string }>(
      `/workspace/sessions/${encodeURIComponent(sessionId)}/workspace/rehydrate`
    );
  },
};
