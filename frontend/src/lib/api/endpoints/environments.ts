/** Environments: shared sandbox setups (backend /environments). */

import { apiClient } from '../client';
import { Environment, EnvironmentSchema, EnvironmentWrite } from '../types/environment.types';

export const environmentsAPI = {
  list: async (): Promise<Environment[]> => EnvironmentSchema.array().parse(await apiClient.get('/environments')),
  create: async (body: EnvironmentWrite): Promise<Environment> =>
    EnvironmentSchema.parse(await apiClient.post('/environments', body)),
  update: async (id: string, body: EnvironmentWrite): Promise<Environment> =>
    EnvironmentSchema.parse(await apiClient.put(`/environments/${encodeURIComponent(id)}`, body)),
  delete: async (id: string): Promise<void> => {
    await apiClient.delete(`/environments/${encodeURIComponent(id)}`);
  },
};
