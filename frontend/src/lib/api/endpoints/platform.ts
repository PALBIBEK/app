/**
 * Platform status API — system BFF only (never probe AI URLs from the browser).
 */

import { apiClient } from '../client';
import type { PlatformStatus } from '../types/platform.types';

export const platformAPI = {
  getStatus: async (): Promise<PlatformStatus> => {
    return apiClient.get<PlatformStatus>('/platform/status');
  },
};
