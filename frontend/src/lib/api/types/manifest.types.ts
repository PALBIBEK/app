/**
 * A harness manifest, as the backend stores drafts and releases (schemas/harness_manifest.py).
 */

import { z } from 'zod';

export const PromptSectionSchema = z.object({
  id: z.string(),
  text: z.string(),
});

export const ManifestSchema = z.object({
  prompt: z.object({
    persona: z.string(),
    sections: z.array(PromptSectionSchema),
  }),
  tools: z.array(z.string()),
  model: z.string(),
  sandbox: z.object({
    provider: z.enum(['none', 'local_process', 'local_docker', 'e2b']),
    file_effects: z.enum(['read_only', 'workspace_write', 'danger_full_access']),
    approval_default: z.enum(['ask', 'never']),
  }),
  memory: z.object({
    compaction: z.boolean(),
  }),
  policy: z.object({
    max_steps: z.number(),
    max_cost_per_session: z.number(),
    max_tokens_per_turn: z.number(),
    require_approval: z.array(z.string()),
    approval_timeout_s: z.number(),
    on_approval_timeout: z.enum(['deny', 'allow']),
    repetition: z.enum(['abort', 'warn', 'off']),
    oscillation: z.enum(['abort', 'warn', 'off']),
    stall_iterations: z.number(),
  }),
});

export const ManifestViewSchema = z.object({
  manifest: ManifestSchema,
  published_version: z.number().nullable(),
  unpublished_changes: z.boolean(),
});

export const ReleaseSchema = z.object({
  version: z.number(),
  manifest: ManifestSchema,
  composition_id: z.string(),
  composition_digest: z.string(),
  harness_version: z.string(),
  runtime: z.string(),
  created_at: z.string(),
});

export const PublishResponseSchema = z.object({
  release: ReleaseSchema,
  created: z.boolean(),
});

export const CatalogToolSchema = z.object({
  name: z.string(),
  description: z.string().default(''),
  tool_class: z.string(),
});

export const CatalogModelSchema = z
  .object({
    id: z.string(),
    label: z.string().nullish(),
    name: z.string().nullish(),
    context_window: z.number().nullish(),
    cost: z.object({ input: z.number().nullish(), output: z.number().nullish() }).partial().nullish(),
  })
  .passthrough();

export type Manifest = z.infer<typeof ManifestSchema>;
export type ManifestView = z.infer<typeof ManifestViewSchema>;
export type Release = z.infer<typeof ReleaseSchema>;
export type PublishResponse = z.infer<typeof PublishResponseSchema>;
export type CatalogTool = z.infer<typeof CatalogToolSchema>;
export type CatalogModel = z.infer<typeof CatalogModelSchema>;

/** The builder's tabs, which are also the manifest's sections. */
export const SECTIONS = ['prompt', 'tools', 'model', 'sandbox', 'memory', 'policy'] as const;
export type Section = (typeof SECTIONS)[number];
