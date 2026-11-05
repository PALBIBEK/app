/**
 * The model vendors the platform can call (``GET /api/v1/model-providers``, owners and admins):
 * each with whether it has a key and where from, never the key itself; and a local server's
 * address and the models it runs.
 */

import { z } from 'zod';

export const ModelProviderSchema = z.object({
  id: z.string(),
  label: z.string(),
  base_url: z.string().nullish(),
  key_required: z.boolean(),
  /** The vendor checks a key before it is kept (else it is saved unchecked). */
  key_checked: z.boolean(),
  configured: z.boolean(),
  /** set: from here; removed: removed here (the deployment's key is not used either);
   * environment: the deployment's; absent: none. */
  source: z.enum(['set', 'removed', 'environment', 'absent']),
  updated_at: z.number().nullish(),
  updated_by: z.string().nullish(),
  ready: z.boolean(),
  address_settable: z.boolean().default(false),
  address_source: z.enum(['set', 'environment', 'absent']).nullish(),
  models: z.array(z.string()).nullish(),
  /** Why the local server's list could not be read, when it could not. */
  unreachable: z.string().nullish(),
});

export const ModelProvidersSchema = z.object({
  /** Keys can be set here (else they are the deployment's environment's). */
  editable: z.boolean(),
  vendors: z.array(ModelProviderSchema),
});

export type ModelProvider = z.infer<typeof ModelProviderSchema>;
export type ModelProviders = z.infer<typeof ModelProvidersSchema>;
