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
  /** Earlier messages of its conversation, replayed before `input`. */
  history: string[];
  /** What a good answer does, in a person's words. */
  expected: string | null;
  source_session_id: string | null;
  source_run_id: string | null;
  source_version: number | null;
  source_status: string | null;
  /** Kept from the optimizer: its conversation is never evidence a change is learnt from. */
  held_out: boolean;
  created_at: string;
}

/** What saving an issue's runs to a dataset did. */
export interface AddIssueResult {
  added: DatasetItem[];
  /** Runs already saved, with no message to replay, or of a session you cannot open. */
  skipped: number;
  /** Runs not tried because the dataset filled up. */
  left_out: number;
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
  /** Replays of each saved run. */
  trials: number;
  /** Replays in all (items × trials). */
  runs: number;
  /** Replays that answered with no failed check or judge. */
  passed: number;
  /** Saved runs that passed every trial (pass^k: consistency). */
  pass_all: number;
  /** Saved runs that passed at least one trial (pass@k). */
  pass_any: number;
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
  /** Which replay of the saved run (0-based). */
  trial: number;
  /** Messages replayed: the earlier ones of the conversation, then the saved run's. */
  turns: number;
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
  a_trials: ExperimentItemResult[];
  b_trials: ExperimentItemResult[];
  /** Share of the saved run's trials that passed; null until all finished. */
  a_pass_rate: number | null;
  b_pass_rate: number | null;
  held_out: boolean;
  change: 'better' | 'worse' | 'same' | 'unknown';
}

export interface ExperimentComparison {
  a: Experiment;
  b: Experiment;
  rows: ComparisonRow[];
  better: number;
  worse: number;
}

// --- proposals: a change learned from traces, gated on saved runs, approved by a person ----

export type ProposalStatus =
  | 'drafting'
  | 'evaluating'
  | 'awaiting_approval'
  | 'refused'
  | 'no_change'
  | 'failed'
  | 'approved'
  | 'rejected';

export interface PromptView {
  persona: string;
  sections: { id: string; text: string }[];
  /** The notes the improvement loop keeps, sent after the sections. */
  learned?: { id: string; text: string }[];
}

export interface EvidenceExample {
  /** The product session of the failing run, when it is one of yours. */
  session_id: string | null;
  run_id: string | null;
  input: string | null;
  summary: string | null;
  reason: string | null;
}

export interface Evidence {
  title: string;
  runs: number;
  examples: EvidenceExample[];
}

export interface Gate {
  passed: boolean;
  /** Why it passed or not, in sentences. */
  reasons: string[];
  better: number;
  worse: number;
  items: { item_id: string; input: string; change: 'better' | 'worse' | 'same' | 'unknown'; held_out?: boolean }[];
  held_out_better?: number;
  held_out_worse?: number;
}

/** One edit to the learnt notes. */
export interface PromptEdit {
  action: 'add' | 'update' | 'delete' | string;
  /** The note's id. */
  id: string;
  /** Its text after the edit (before it, for a delete). */
  text: string;
  /** Its text before an update. */
  old: string | null;
  reason: string;
}

/** How an approved change does live against the version before it. */
export interface ReleaseWatch {
  status: 'watching' | 'held' | 'rolled_back' | 'superseded' | 'ended' | string;
  previous_version: number | null;
  checks: number;
  failed_checks: number;
  seen_runs: number;
  note: string | null;
  /** The version that made the previous one live again. */
  rolled_back_to: number | null;
}

export interface SearchRound {
  round: number;
  /** The round whose candidate it edits (0: the live version). */
  parent: number;
  rationale: string | null;
  edits: PromptEdit[];
  experiment_id: string | null;
  /** Better than its parent on the saved runs it may learn from. */
  kept: boolean;
  why: string;
}

export interface Proposal {
  id: string;
  agent_id: string;
  agent_name: string | null;
  dataset_id: string;
  dataset_name: string | null;
  status: ProposalStatus;
  error: string | null;
  notes: string | null;
  base_version: number;
  rationale: string | null;
  /** Section ids the change touches ("persona" for the persona line). */
  changed: string[];
  /** The itemized edits the reflection made, in order. */
  edits: PromptEdit[];
  /** Edits it asked for that could not be applied, and why. */
  skipped_edits: string[];
  /** What should improve, in the reflection's words. */
  expected_outcome: string | null;
  /** Candidates the search could try. */
  max_rounds: number;
  /** Each candidate it tried. */
  rounds: SearchRound[];
  gate: Gate | null;
  release_version: number | null;
  watch?: ReleaseWatch | null;
  created_at: string;
  decided_at: string | null;
}

export interface ProposalDetail extends Proposal {
  base_prompt: PromptView;
  candidate_prompt: PromptView | null;
  evidence: Evidence[];
  baseline: Experiment | null;
  candidate: Experiment | null;
}

/** Proposing a change by itself when the agent's biggest issue keeps growing. */
export interface AutoImproveInput {
  dataset_id: string;
  enabled: boolean;
  /** Failing runs of one issue since the last attempt that start one. */
  min_runs: number;
  /** Least time between attempts. */
  cooldown_minutes: number;
  rounds: number;
}

export interface AutoImprove extends AutoImproveInput {
  agent_id: string;
  last_attempt_at: string | null;
  last_proposal_id: string | null;
  /** What the last check found, in a sentence. */
  last_note: string | null;
}
