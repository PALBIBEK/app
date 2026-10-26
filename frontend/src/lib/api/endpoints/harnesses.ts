/**
 * harnesses.ts
 *
 * GREEN Phase: Harnesses API Endpoints
 * Reference: 03-api-routing-patterns.md (RESTful conventions)
 * Reference: FRONTEND_BUILD_CONFIG.md (TypeScript patterns)
 *
 * Harnesses represent agent configurations combining model, tools, memory, and prompts.
 * This module provides CRUD operations for harness management.
 *
 * SECURITY NOTES (VibeSec Audit):
 * - All harness IDs should use UUIDs (non-guessable)
 * - Authorization validated server-side for all operations
 * - Model selection validated against allowed providers (server-side)
 * - System prompts sanitized for injection prevention (server-side)
 * - Tool and memory references validated for ownership (server-side)
 */

import { apiClient } from '../client';
import {
  Harness,
  HarnessBindRequest,
  HarnessCreate,
  HarnessUpdate,
  HarnessListResponse,
  HarnessSchema,
} from '../types/harness.types';
import {
  CatalogModelSchema,
  CatalogToolSchema,
  ManifestViewSchema,
  PublishResponseSchema,
  ReleaseSchema,
  type CatalogModel,
  type CatalogTool,
  type Manifest,
  type ManifestView,
  type PublishResponse,
  type Release,
} from '../types/manifest.types';

/**
 * SECURITY: Whitelist of allowed fields for harness creation/update
 * Prevents mass assignment attacks
 */
const ALLOWED_HARNESS_FIELDS = [
  'name',
  'description',
  'model',
  'mode',
  'tools',
  'memory',
  'system_prompt',
  'temperature',
  'max_tokens',
] as const;

/**
 * Sanitize harness data to prevent mass assignment
 * Only allows whitelisted fields through
 */
function sanitizeHarnessData<T extends Partial<HarnessCreate>>(data: T): Partial<HarnessCreate> {
  const sanitized: Partial<HarnessCreate> = {};
  for (const field of ALLOWED_HARNESS_FIELDS) {
    if (field in data) {
      (sanitized as Record<string, unknown>)[field] = (data as Record<string, unknown>)[field];
    }
  }
  return sanitized;
}

export const harnessesAPI = {
  /**
   * List all harnesses with optional filters
   * GET /harnesses
   */
  list: async (params?: { page?: number; page_size?: number; search?: string }): Promise<HarnessListResponse> => {
    const response = await apiClient.get<HarnessListResponse>('/harnesses', { params });
    return {
      ...response,
      data: response.data.map((item) => HarnessSchema.parse(item)),
    };
  },

  /**
   * Get harness by ID
   * GET /harnesses/:id
   */
  getById: async (id: string): Promise<Harness> => {
    const response = await apiClient.get<Harness>(`/harnesses/${id}`);
    return HarnessSchema.parse(response);
  },

  /**
   * Create a new harness
   * POST /harnesses
   *
   * SECURITY: Data is sanitized to prevent mass assignment
   */
  create: async (data: HarnessCreate): Promise<Harness> => {
    const sanitizedData = sanitizeHarnessData(data);
    const response = await apiClient.post<Harness>('/harnesses', sanitizedData);
    return HarnessSchema.parse(response);
  },

  /**
   * Update an existing harness
   * PUT /harnesses/:id
   *
   * SECURITY: Data is sanitized to prevent mass assignment
   */
  update: async (id: string, data: HarnessUpdate): Promise<Harness> => {
    const sanitizedData = sanitizeHarnessData(data);
    const response = await apiClient.put<Harness>(`/harnesses/${id}`, sanitizedData);
    return HarnessSchema.parse(response);
  },

  /**
   * Delete a harness
   * DELETE /harnesses/:id
   */
  delete: async (id: string): Promise<void> => {
    await apiClient.delete(`/harnesses/${id}`);
  },

  /**
   * Bind harness to offered composition (D3)
   * POST /harnesses/:id/bind
   */
  bind: async (id: string, data: HarnessBindRequest = {}): Promise<Harness> => {
    const response = await apiClient.post<Harness>(`/harnesses/${id}/bind`, data);
    return HarnessSchema.parse(response);
  },

  /** The draft manifest (a new harness starts as the shipped coding agent). */
  getManifest: async (id: string): Promise<ManifestView> =>
    ManifestViewSchema.parse(await apiClient.get(`/harnesses/${id}/manifest`)),

  saveManifest: async (id: string, manifest: Manifest): Promise<ManifestView> =>
    ManifestViewSchema.parse(await apiClient.put(`/harnesses/${id}/manifest`, manifest)),

  /** Every check publishing the saved draft would run; nothing is published. */
  checkDraft: async (id: string): Promise<DraftCheckResult> => apiClient.post<DraftCheckResult>(`/harnesses/${id}/check`),

  /** Publish the draft as the next release (an unchanged draft publishes nothing). */
  publish: async (id: string): Promise<PublishResponse> =>
    PublishResponseSchema.parse(await apiClient.post(`/harnesses/${id}/publish`)),

  releases: async (id: string): Promise<Release[]> =>
    ReleaseSchema.array().parse(await apiClient.get(`/harnesses/${id}/releases`)),
};

/** What a harness can be built from, as the supervisor offers it now. */
export const catalogAPI = {
  tools: async (): Promise<CatalogTool[]> => CatalogToolSchema.array().parse(await apiClient.get('/catalog/tools')),
  models: async (): Promise<CatalogModel[]> => CatalogModelSchema.array().parse(await apiClient.get('/catalog/models')),
};

export interface DraftCheck {
  /** The builder tab it is about: prompt, tools, model, sandbox, memory, policy. */
  section: string;
  check: string;
  ok: boolean;
  message: string;
  code: string | null;
}

export interface DraftCheckResult {
  ok: boolean;
  checks: DraftCheck[];
}
