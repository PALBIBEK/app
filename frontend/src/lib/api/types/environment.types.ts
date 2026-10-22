/** Environments: named sandbox setups several harnesses share (schemas/environment.py). */

import { z } from 'zod';

import { SandboxSpecSchema } from './manifest.types';

export const EnvironmentSchema = z.object({
  id: z.string(),
  name: z.string(),
  description: z.string(),
  sandbox: SandboxSpecSchema,
  used_by: z.array(z.object({ id: z.string(), name: z.string(), published: z.boolean() })),
  created_at: z.string(),
  updated_at: z.string(),
});

export type Environment = z.infer<typeof EnvironmentSchema>;

export interface EnvironmentWrite {
  name: string;
  description: string;
  sandbox: z.infer<typeof SandboxSpecSchema>;
}
