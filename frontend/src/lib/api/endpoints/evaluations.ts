/**
 * Datasets of saved runs and experiments that replay them on a version (BFF, under /traces).
 */

import { apiClient } from '../client';
import type {
  DatasetDetail,
  DatasetItem,
  DatasetSummary,
  Experiment,
  ExperimentComparison,
  ExperimentDetail,
  Proposal,
  ProposalDetail,
} from '../types/evaluation.types';

const seg = encodeURIComponent;

export const evaluationsAPI = {
  datasets: (agentId: string): Promise<DatasetSummary[]> =>
    apiClient.get<DatasetSummary[]>(`/traces/agents/${seg(agentId)}/datasets`),

  createDataset: (agentId: string, body: { name: string; description?: string | null }): Promise<DatasetSummary> =>
    apiClient.post<DatasetSummary>(`/traces/agents/${seg(agentId)}/datasets`, body),

  dataset: (datasetId: string): Promise<DatasetDetail> => apiClient.get<DatasetDetail>(`/traces/datasets/${seg(datasetId)}`),

  deleteDataset: (datasetId: string): Promise<void> => apiClient.delete<void>(`/traces/datasets/${seg(datasetId)}`),

  /** Save one of the user's runs to the dataset, with what a good answer does (optional). */
  addRun: (datasetId: string, body: { session_id: string; run_id: string; expected?: string | null }): Promise<DatasetItem> =>
    apiClient.post<DatasetItem>(`/traces/datasets/${seg(datasetId)}/items`, body),

  deleteItem: (datasetId: string, itemId: string): Promise<void> =>
    apiClient.delete<void>(`/traces/datasets/${seg(datasetId)}/items/${seg(itemId)}`),

  /** Replay every saved run on a version (none: the live one); answers at once. */
  startExperiment: (datasetId: string, version: number | null): Promise<Experiment> =>
    apiClient.post<Experiment>(`/traces/datasets/${seg(datasetId)}/experiments`, { version }),

  experiment: (experimentId: string): Promise<ExperimentDetail> =>
    apiClient.get<ExperimentDetail>(`/traces/experiments/${seg(experimentId)}`),

  cancelExperiment: (experimentId: string): Promise<void> =>
    apiClient.post<void>(`/traces/experiments/${seg(experimentId)}/cancel`),

  compare: (a: string, b: string): Promise<ExperimentComparison> =>
    apiClient.get<ExperimentComparison>(`/traces/experiments/compare?a=${seg(a)}&b=${seg(b)}`),

  /** Learn a change from the agent's failing runs and measure it on a dataset; answers at once. */
  propose: (agentId: string, body: { dataset_id: string; notes?: string | null }): Promise<Proposal> =>
    apiClient.post<Proposal>(`/traces/agents/${seg(agentId)}/proposals`, body),

  proposals: (params: { agentId?: string; status?: string } = {}): Promise<Proposal[]> => {
    const q = new URLSearchParams();
    if (params.agentId) q.set('agent_id', params.agentId);
    if (params.status) q.set('status_filter', params.status);
    const qs = q.toString();
    return apiClient.get<Proposal[]>(`/traces/proposals${qs ? `?${qs}` : ''}`);
  },

  proposal: (id: string): Promise<ProposalDetail> => apiClient.get<ProposalDetail>(`/traces/proposals/${seg(id)}`),

  approveProposal: (id: string): Promise<Proposal> => apiClient.post<Proposal>(`/traces/proposals/${seg(id)}/approve`),

  rejectProposal: (id: string): Promise<Proposal> => apiClient.post<Proposal>(`/traces/proposals/${seg(id)}/reject`),
};
