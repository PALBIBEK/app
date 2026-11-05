/**
 * modelsAPI — what a session's model can be chosen from (product BFF only).
 */

import { apiClient } from '../client';
import { ModelChoicesSchema, type ModelChoices } from '../types/model.types';

export const modelsAPI = {
  choices: async (): Promise<ModelChoices> =>
    ModelChoicesSchema.parse(await apiClient.get('/catalog/model-choices')),
};
