/**
 * Datasets and experiments: React Query hooks. An experiment still replaying refreshes
 * itself (and its dataset's page) every few seconds.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';

import { useAuthStore } from '@/lib/store/authStore';

import { evaluationsAPI } from '../endpoints/evaluations';
import type { ExperimentStatus } from '../types/evaluation.types';

const GOING: readonly ExperimentStatus[] = ['queued', 'running'];
const REFRESH_MS = 3000;

export const evaluationKeys = {
  all: ['evaluations'] as const,
  datasets: (agentId: string) => [...evaluationKeys.all, 'datasets', agentId] as const,
  dataset: (id: string) => [...evaluationKeys.all, 'dataset', id] as const,
  experiment: (id: string) => [...evaluationKeys.all, 'experiment', id] as const,
  compare: (a: string, b: string) => [...evaluationKeys.all, 'compare', a, b] as const,
};

function message(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: { message?: string } | string } } })?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  return detail?.message ?? (err instanceof Error ? err.message : fallback);
}

export function useDatasets(agentId: string | undefined) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  return useQuery({
    queryKey: evaluationKeys.datasets(agentId ?? ''),
    queryFn: () => evaluationsAPI.datasets(agentId!),
    enabled: isAuthenticated && !!agentId,
  });
}

export function useDataset(datasetId: string | undefined) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  return useQuery({
    queryKey: evaluationKeys.dataset(datasetId ?? ''),
    queryFn: () => evaluationsAPI.dataset(datasetId!),
    enabled: isAuthenticated && !!datasetId,
    refetchInterval: (q) => (q.state.data?.experiments.some((e) => GOING.includes(e.status)) ? REFRESH_MS : false),
  });
}

export function useExperiment(experimentId: string | undefined) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  return useQuery({
    queryKey: evaluationKeys.experiment(experimentId ?? ''),
    queryFn: () => evaluationsAPI.experiment(experimentId!),
    enabled: isAuthenticated && !!experimentId,
    refetchInterval: (q) => (q.state.data && GOING.includes(q.state.data.experiment.status) ? REFRESH_MS : false),
  });
}

export function useComparison(a: string | undefined, b: string | undefined) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  return useQuery({
    queryKey: evaluationKeys.compare(a ?? '', b ?? ''),
    queryFn: () => evaluationsAPI.compare(a!, b!),
    enabled: isAuthenticated && !!a && !!b,
  });
}

export function useCreateDataset(agentId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; description?: string | null }) => evaluationsAPI.createDataset(agentId, body),
    onSuccess: () => void client.invalidateQueries({ queryKey: evaluationKeys.datasets(agentId) }),
    onError: (err) => toast.error(message(err, 'Could not create the dataset')),
  });
}

export function useAddRun() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ datasetId, ...body }: { datasetId: string; session_id: string; run_id: string; expected?: string | null }) =>
      evaluationsAPI.addRun(datasetId, body),
    onSuccess: () => {
      toast.success('Saved to the dataset');
      void client.invalidateQueries({ queryKey: evaluationKeys.all });
    },
    onError: (err) => toast.error(message(err, 'Could not save the run')),
  });
}

export function useDeleteItem(datasetId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (itemId: string) => evaluationsAPI.deleteItem(datasetId, itemId),
    onSuccess: () => void client.invalidateQueries({ queryKey: evaluationKeys.dataset(datasetId) }),
    onError: (err) => toast.error(message(err, 'Could not remove the run')),
  });
}

export function useStartExperiment(datasetId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (version: number | null) => evaluationsAPI.startExperiment(datasetId, version),
    onSuccess: () => void client.invalidateQueries({ queryKey: evaluationKeys.dataset(datasetId) }),
    onError: (err) => toast.error(message(err, 'Could not start the experiment')),
  });
}

export function useCancelExperiment(experimentId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => evaluationsAPI.cancelExperiment(experimentId),
    onSuccess: () => void client.invalidateQueries({ queryKey: evaluationKeys.all }),
  });
}
