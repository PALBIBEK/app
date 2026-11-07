/**
 * Approvals inbox, sandboxes, usage, runners, secrets, the browser vault, publish checks and the fleet.
 */

import { apiClient } from '../client';
import { ModelProviderSchema, ModelProvidersSchema, type ModelProvider, type ModelProviders } from '../types/provider.types';
import type {
  ArtifactKind,
  FleetStatus,
  InboxItem,
  PublishCheckResult,
  RolloutStatus,
  Runner,
  RunnerToken,
  SandboxItem,
  UsageBy,
  UsageReport,
  VaultAddressIn,
  VaultCardIn,
  VaultItem,
  VaultLoginIn,
} from '../types/operate.types';

const seg = (name: string) => name.split('/').map(encodeURIComponent).join('/');

export const operateAPI = {
  approvalInbox: (): Promise<InboxItem[]> => apiClient.get<InboxItem[]>('/approvals'),

  sandboxes: (opts: { sessionId?: string; includeEnded?: boolean } = {}): Promise<SandboxItem[]> =>
    apiClient.get<SandboxItem[]>('/sandboxes', {
      params: {
        ...(opts.sessionId ? { session_id: opts.sessionId } : {}),
        ...(opts.includeEnded ? { include_ended: true } : {}),
      },
    }),

  usage: (opts: { by?: UsageBy; sinceMs?: number; untilMs?: number } = {}): Promise<UsageReport> =>
    apiClient.get<UsageReport>('/usage', {
      params: {
        by: opts.by ?? 'day',
        ...(opts.sinceMs != null ? { since_ms: opts.sinceMs } : {}),
        ...(opts.untilMs != null ? { until_ms: opts.untilMs } : {}),
      },
    }),

  runners: (): Promise<Runner[]> => apiClient.get<Runner[]>('/runners'),
  registerRunner: (name: string): Promise<RunnerToken> =>
    apiClient.post<RunnerToken>(`/runners/${encodeURIComponent(name)}`),
  removeRunner: (name: string): Promise<void> => apiClient.delete<void>(`/runners/${encodeURIComponent(name)}`),

  modelProviders: async (): Promise<ModelProviders> => ModelProvidersSchema.parse(await apiClient.get('/model-providers')),
  setProviderKey: async (vendor: string, key: string): Promise<ModelProvider> =>
    ModelProviderSchema.parse(await apiClient.put(`/model-providers/${seg(vendor)}/key`, { key })),
  removeProviderKey: async (vendor: string): Promise<ModelProvider> =>
    ModelProviderSchema.parse(await apiClient.delete(`/model-providers/${seg(vendor)}/key`)),
  setProviderAddress: async (vendor: string, baseUrl: string): Promise<ModelProvider> =>
    ModelProviderSchema.parse(await apiClient.put(`/model-providers/${seg(vendor)}/base-url`, { base_url: baseUrl })),
  secretNames: async (): Promise<string[]> => (await apiClient.get<{ names: string[] }>('/secrets')).names,
  putSecret: (name: string, value: string): Promise<void> => apiClient.put<void>(`/secrets/${seg(name)}`, { value }),
  deleteSecret: (name: string): Promise<void> => apiClient.delete<void>(`/secrets/${seg(name)}`),

  vaultItems: async (): Promise<VaultItem[]> => (await apiClient.get<{ items: VaultItem[] }>('/browser-vault')).items,
  saveVaultLogin: (login: VaultLoginIn): Promise<void> => apiClient.put<void>('/browser-vault/logins', login),
  saveVaultCard: (card: VaultCardIn): Promise<void> => apiClient.put<void>('/browser-vault/cards', card),
  saveVaultAddress: (address: VaultAddressIn): Promise<void> => apiClient.put<void>('/browser-vault/addresses', address),
  deleteVaultItem: (handle: string): Promise<void> => apiClient.delete<void>(`/browser-vault/${encodeURIComponent(handle)}`),

  checkArtifact: (kind: ArtifactKind, body: Record<string, unknown>): Promise<PublishCheckResult> =>
    apiClient.post<PublishCheckResult>(`/catalog/${kind}/check`, body),

  workers: (): Promise<FleetStatus> => apiClient.get<FleetStatus>('/ops/workers'),
  rollout: (): Promise<RolloutStatus> => apiClient.get<RolloutStatus>('/ops/rollout'),
};
