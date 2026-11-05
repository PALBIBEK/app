/**
 * A model vendor as the gateway reports it (``GET /api/v1/model-providers``): whether it has a
 * key and where from, and whether its models can run. Never the key.
 */

import { z } from 'zod';

export const ModelProviderSchema = z
  .object({
    id: z.string(),
    label: z.string(),
    base_url: z.string(),
    key_required: z.boolean(),
    /** The vendor documents a call to check a key with; without one a key is kept unchecked. */
    key_checked: z.boolean(),
    configured: z.boolean(),
    /** ``set`` here, ``removed`` here, from the gateway's ``environment``, or ``absent``. */
    source: z.enum(['set', 'removed', 'environment', 'absent']),
    updated_at: z.number().nullish(),
    updated_by: z.string().nullish(),
    ready: z.boolean(),
    /** On a save only: whether the vendor accepted the key (false: kept unchecked). */
    checked: z.boolean().optional(),
    /** The local server only: the models it runs, and why its list could not be read. */
    models: z.array(z.string()).optional(),
    error: z.string().nullish(),
  })
  .passthrough();

export const ModelProvidersSchema = z.object({
  /** Keys can be set here; false when this deployment takes them from the gateway's environment. */
  editable: z.boolean(),
  vendors: z.array(ModelProviderSchema),
});

export type ModelProvider = z.infer<typeof ModelProviderSchema>;
export type ModelProviders = z.infer<typeof ModelProvidersSchema>;
