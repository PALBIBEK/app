/**
 * Approvals inbox, sandboxes, usage, runners, secrets, publish checks and the fleet.
 */

import { apiClient } from '../client';
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

  secretNames: async (): Promise<string[]> => (await apiClient.get<{ names: string[] }>('/secrets')).names,
  putSecret: (name: string, value: string): Promise<void> => apiClient.put<void>(`/secrets/${seg(name)}`, { value }),
  deleteSecret: (name: string): Promise<void> => apiClient.delete<void>(`/secrets/${seg(name)}`),

  checkArtifact: (kind: ArtifactKind, body: Record<string, unknown>): Promise<PublishCheckResult> =>
    apiClient.post<PublishCheckResult>(`/catalog/${kind}/check`, body),

  workers: (): Promise<FleetStatus> => apiClient.get<FleetStatus>('/ops/workers'),
  rollout: (): Promise<RolloutStatus> => apiClient.get<RolloutStatus>('/ops/rollout'),
};
