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

/** One saved version of a file (newest first in a history). */
export interface FileVersion {
  version: number;
  content_sha256: string;
  size_bytes: number;
  mime_type?: string | null;
  created_at: string;
  author?: string | null;
  author_email?: string | null;
  restored_from?: number | null;
  /** `run:<session>/<turn>` (the agent's edit) or `artifact:<id>` (a saved run output). */
  source?: string | null;
  current: boolean;
}

/** A workspace file attached to a conversation, pinned to one version. */
export interface Attachment {
  id: string;
  scope_id: string;
  path: string;
  version: number;
  content_sha256: string;
  size_bytes: number;
  mime_type?: string | null;
  name: string;
  /** Where the agent reads it (read-only): `.attachments/<name>`. */
  sandbox_path: string;
  attached_by?: string | null;
  created_at: string;
}

/** A file a run made for the user (from its `.outputs/` folder). */
export interface Artifact {
  id: string;
  turn_id: string;
  name: string;
  content_sha256: string;
  size_bytes: number;
  mime_type?: string | null;
  created_at: string;
  saved_scope_id?: string | null;
  saved_path?: string | null;
  saved_version?: number | null;
}

export interface RunInput {
  kind: 'attachment' | 'workspace' | string;
  scope_id: string;
  path: string;
  version: number;
  content_sha256: string;
}

export interface ScopeMember {
  principal_id: string;
  grants: string[];
  email?: string | null;
  name?: string | null;
}

const filePath = (path: string) => path.split('/').map(encodeURIComponent).join('/');

/** What the browser will send; the server answers how (see lib/workspace/upload.ts). */
export interface UploadStart {
  path: string;
  size_bytes: number;
  content_sha256: string;
  mime_type?: string;
  expected_version?: number;
}

export interface UploadPlan {
  status: 'upload' | 'committed' | 'proxy';
  max_bytes: number;
  upload_token?: string | null;
  url?: string | null;
  method?: string | null;
  headers?: Record<string, string> | null;
  expires_in_s?: number | null;
  file?: WorkspaceFile | null;
}

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

  /** Step 1 of a direct upload: a signed URL for the browser to PUT the file to. */
  startUpload: (scopeId: string, body: UploadStart): Promise<UploadPlan> =>
    apiClient.post<UploadPlan>(`/workspace/scopes/${encodeURIComponent(scopeId)}/uploads`, body),

  /** Step 3: the server checks what arrived and records the file. */
  completeUpload: (scopeId: string, uploadToken: string): Promise<WorkspaceFile> =>
    apiClient.post<WorkspaceFile>(`/workspace/scopes/${encodeURIComponent(scopeId)}/uploads/complete`, {
      upload_token: uploadToken,
    }),

  /** Through the API (when storage cannot take a direct upload). */
  uploadFile: async (
    scopeId: string,
    path: string,
    file: File | Blob,
    mimeType?: string,
    onProgress?: (fraction: number) => void
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
      {
        headers: { 'Content-Type': undefined },
        onUploadProgress: (e: { loaded: number; total?: number }) => onProgress?.(e.total ? e.loaded / e.total : 0),
      }
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

  /** Every saved version of a file, newest first (kept after it is deleted). */
  listVersions: (scopeId: string, path: string): Promise<FileVersion[]> =>
    apiClient.get<FileVersion[]>(`/workspace/scopes/${encodeURIComponent(scopeId)}/history/${filePath(path)}`),

  /** One version's bytes. */
  getVersion: (scopeId: string, path: string, version: number): Promise<Blob> =>
    apiClient.get<Blob>(`/workspace/scopes/${encodeURIComponent(scopeId)}/history/${filePath(path)}/@${version}`, {
      responseType: 'blob',
    }),

  /** Make an earlier version current again (a new version; nothing is lost). */
  restoreVersion: (scopeId: string, path: string, version: number): Promise<WorkspaceFile> =>
    apiClient.post<WorkspaceFile>(`/workspace/scopes/${encodeURIComponent(scopeId)}/restore/${filePath(path)}`, { version }),

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

const sess = (id: string) => `/workspace/sessions/${encodeURIComponent(id)}`;

/** A conversation's attachments, its runs' inputs and its artifacts. */
export const sessionFilesAPI = {
  attachments: (sessionId: string): Promise<Attachment[]> => apiClient.get<Attachment[]>(`${sess(sessionId)}/attachments`),

  /** Attach a file from the session's workspace (its current version unless `version`). */
  attach: (sessionId: string, path: string, version?: number): Promise<Attachment> =>
    apiClient.post<Attachment>(`${sess(sessionId)}/attachments`, version ? { path, version } : { path }),

  detach: async (sessionId: string, attachmentId: string): Promise<void> => {
    await apiClient.delete(`${sess(sessionId)}/attachments/${encodeURIComponent(attachmentId)}`);
  },

  artifacts: (sessionId: string): Promise<Artifact[]> => apiClient.get<Artifact[]>(`${sess(sessionId)}/artifacts`),

  artifactContent: (sessionId: string, artifactId: string): Promise<Blob> =>
    apiClient.get<Blob>(`${sess(sessionId)}/artifacts/${encodeURIComponent(artifactId)}/content`, { responseType: 'blob' }),

  /** Save a run's output into the workspace at `path` (a new version, its source recorded). */
  save: (sessionId: string, artifactId: string, path: string): Promise<{ artifact: Artifact; path: string; version: number }> =>
    apiClient.post(`${sess(sessionId)}/artifacts/${encodeURIComponent(artifactId)}/save`, { path }),

  runInputs: (sessionId: string, turnId: string): Promise<RunInput[]> =>
    apiClient.get<RunInput[]>(`${sess(sessionId)}/runs/${encodeURIComponent(turnId)}/inputs`),
};
