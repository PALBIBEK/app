/**
 * Datasets of saved runs, and experiments that replay them on an exact agent version.
 */

export interface DatasetSummary {
  id: string;
  agent_id: string;
  name: string;
  description: string | null;
  items: number;
  experiments: number;
  created_at: string;
}

export interface DatasetItem {
  id: string;
  input: string;
  /** What a good answer does, in a person's words. */
  expected: string | null;
  source_session_id: string | null;
  source_run_id: string | null;
  source_version: number | null;
  source_status: string | null;
  created_at: string;
}

export interface ExperimentStats {
  items: number;
  finished: number;
  answered: number;
  failed: number;
  needs_approval: number;
  /** Runs that failed at least one automatic check. */
  checks_failed: number;
  judge_pass: number;
  judge_fail: number;
  avg_cost: number | null;
  avg_duration_ms: number | null;
}

export type ExperimentStatus = 'queued' | 'running' | 'done' | 'failed' | 'cancelled';

export interface Experiment {
  id: string;
  dataset_id: string;
  label: string;
  release_version: number | null;
  composition_digest: string | null;
  status: ExperimentStatus;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  stats: ExperimentStats | null;
}

export interface DatasetDetail {
  id: string;
  agent_id: string;
  agent_name: string | null;
  name: string;
  description: string | null;
  items: DatasetItem[];
  experiments: Experiment[];
}

export interface Verdict {
  name: string;
  source: 'check' | 'judge' | string;
  label: string;
  reason: string | null;
}

export interface ExperimentItemResult {
  item_id: string;
  position: number;
  input: string;
  expected: string | null;
  /** How the replay went: done, needs_approval, timed_out, failed, pending, running. */
  status: string;
  error: string | null;
  session_id: string | null;
  run_id: string | null;
  /** How the run ended (ok, error …), once it has. */
  run_status: string | null;
  summary: string | null;
  cost: number | null;
  duration_ms: number | null;
  verdicts: Verdict[];
}

export interface ExperimentDetail {
  experiment: Experiment;
  dataset_name: string;
  results: ExperimentItemResult[];
}

export interface ComparisonRow {
  item_id: string;
  input: string;
  a: ExperimentItemResult | null;
  b: ExperimentItemResult | null;
  change: 'better' | 'worse' | 'same' | 'unknown';
}

export interface ExperimentComparison {
  a: Experiment;
  b: Experiment;
  rows: ComparisonRow[];
  better: number;
  worse: number;
}
