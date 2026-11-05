/**
 * What a person can pick a session's model from (``GET /api/v1/catalog/model-choices``): the
 * models that can run now, each with the vendor that serves it, and every vendor with whether
 * it is ready (a vendor without a key, or a local server without its URL, offers no model).
 */

import { z } from 'zod';

export const ModelChoiceSchema = z
  .object({
    id: z.string(),
    label: z.string().nullish(),
    name: z.string().nullish(),
    vendor: z.string().nullish(),
    context_window: z.number().nullish(),
    cost: z.object({ input: z.number().nullish(), output: z.number().nullish() }).partial().nullish(),
  })
  .passthrough();

export const ModelProviderReadinessSchema = z.object({
  id: z.string(),
  label: z.string().nullish(),
  ready: z.boolean(),
});

export const ModelChoicesSchema = z.object({
  models: z.array(ModelChoiceSchema),
  providers: z.array(ModelProviderReadinessSchema),
});

export type ModelChoice = z.infer<typeof ModelChoiceSchema>;
export type ModelProviderReadiness = z.infer<typeof ModelProviderReadinessSchema>;
export type ModelChoices = z.infer<typeof ModelChoicesSchema>;
