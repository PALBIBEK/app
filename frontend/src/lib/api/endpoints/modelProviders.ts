/**
 * modelProvidersAPI — the platform's model vendors and their keys (owners and admins).
 *
 * A key goes to the model gateway, which alone keeps it; nothing here ever reads one back.
 */

import { apiClient } from '../client';
import {
  ModelProviderSchema,
  ModelProvidersSchema,
  type ModelProvider,
  type ModelProviders,
} from '../types/modelProvider.types';

const seg = (vendor: string): string => encodeURIComponent(vendor);

export const modelProvidersAPI = {
  list: async (): Promise<ModelProviders> => ModelProvidersSchema.parse(await apiClient.get('/model-providers')),
  /** The vendor checks the key first; a key it refuses is not kept (the error says so). */
  setKey: async (vendor: string, key: string): Promise<ModelProvider> =>
    ModelProviderSchema.parse(await apiClient.put(`/model-providers/${seg(vendor)}/key`, { key })),
  /** The vendor is off from now, even with a key in the gateway's environment. */
  removeKey: async (vendor: string): Promise<ModelProvider> =>
    ModelProviderSchema.parse(await apiClient.delete(`/model-providers/${seg(vendor)}/key`)),
};
